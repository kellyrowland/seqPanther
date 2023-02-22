#!/usr/bin/env python
import pysam
import pandas as pd
import numpy as np

import pyfaidx
from os import path, system

from .subs import sub_table
from .indel_frames import indel_frames


def changed_coordinates(params, bam):
    print(f"Analysing {bam}.")
    ref = params["ref"]
    rid = params["rid"]
    tmp_dir = params["tmp_dir"]
    start = params["start"]
    end = params["end"]
    endlen = params["endlen"]
    sequences = params["sequences"]
    ignore_orphans = params["ignore_orphans"]
    min_mapping_quality = params["min_mapping_quality"]
    min_base_quality = params["min_base_quality"]
    min_seq_depth = params["min_seq_depth"]
    max_seq_depth = params["max_seq_depth"]
    alt_nuc_count = params["alt_nuc_count"]
    ignore_overlaps = params["ignore_overlaps"]

    samfile = pysam.AlignmentFile(bam, "rb")
    if rid not in samfile.references:
        print(f"Given reference {rid} not in given bam file {bam}")
        print("List of references")
        print(samfile.references)
        print(f"Ignoring {bam}")
        return

    vcf_file = f'{tmp_dir}/{path.split(bam)[-1].split(".")[0]}.vcf'

    # command = f"bcftools mpileup -x -d 20000000 -m 1 -e 10 -L 10000000 --open-prob 10  -Q 0 -A -B -C0 --annotate FORMAT/AD  -Ob -r  NC_045512.2:20000-25000 --no-BAQ -f NC_045512.2.fasta -o test.bcf K032298-consensus_alignment_sorted.bam"
    command = f"bcftools mpileup --annotate FORMAT/AD -d {max_seq_depth} -L {max_seq_depth} -m 1 -e 40 --open-prob 40 -B -q {min_mapping_quality} -C0 -Q {min_base_quality} -r {rid}:{start}-{end} --no-BAQ -f {ref} -o {vcf_file} {bam}"
    if ignore_overlaps:
        command += ' -x'
    if not ignore_orphans:
        command += ' -A'
    print(command)

    command += " 2>/dev/null"

    system(command)
    vcf = pd.read_table(vcf_file,
                        header=None,
                        comment="#",
                        usecols=[0, 1, 7, 9])
    vcf[9] = vcf[9].apply(lambda x: x.split(':')[1])
    vcf[9] = vcf[9].apply(lambda x: np.array(list(map(int, x.split(',')))))
    vcf['total'] = vcf[9].apply(lambda x: np.sum(x))
    depth = vcf[[1, 'total']].rename(columns={
        1: 'coor',
        'total': 'depth'
    }).sort_values('depth',
                   ascending=False).drop_duplicates('coor').sort_values('coor')

    vcf = vcf[vcf['total'] > min_seq_depth]
    vcf[9] = vcf[9] / vcf['total']  # TODO: Keep a copy of this for plotting
    vcf[9] = vcf[9].apply(lambda x: x[1:])

    vcf = vcf[vcf[9].apply(lambda x: np.sum(x > alt_nuc_count)) > 0]
    # print(vcf)
    # vcf = vcf[vcf[1] == 21764]

    coordinates_with_change = {}
    indel_pos_type_size = {}
    reads_to_remove = {}
    for row in vcf.to_dict('records'):
        is_indel = False
        if row[7].startswith('INDEL'):
            is_indel = True
        start, end = (row[1] - 1, row[1])
        # if not is_indel:
        # continue
        iter = samfile.pileup(
            rid,
            start,
            end,
            ignore_orphans=ignore_orphans,
            min_base_quality=min_base_quality,
            min_mapping_quality=min_mapping_quality,
            ignore_overlaps=ignore_overlaps,
            max_depth=max_seq_depth,
        )
        bases = {}
        read_depth = 0
        for pileupcol in iter:
            # if pileupcol.pos < start - 3:
            # continue
            # print(pileupcol.pos, pileupcol.n)
            if pileupcol.pos != start:
                continue
            read_depth = pileupcol.n
            reads_to_remove_count = {}
            for pread in pileupcol.pileups:
                if not pread.query_position:
                    continue
                add_left = pread.query_position - pread.alignment.query_alignment_start
                add_right = pread.alignment.query_alignment_end - pread.query_position
                if is_indel:
                    if pread.indel and pread.indel % 3 == 0:
                        add_left = 3 - add_left
                        add_left = add_left if add_left > 0 else 0
                        overhang = add_right  # to remove sequence which terminate in between
                        add_right = 4 - add_right
                        add_right = add_right if add_right > 0 else 0

                        if pread.indel < 0:
                            if overhang <= abs(pread.indel):
                                if overhang not in reads_to_remove_count:
                                    reads_to_remove_count[overhang] = 0
                                reads_to_remove_count[overhang] += 1

                            ref_sq = sequences[start + 1 - 3:start + 1 + 3 -
                                               pread.indel].seq
                            read_sq = '-' * add_left + pread.alignment.query_sequence[
                                pread.query_position + 1 - 3 -
                                add_left:pread.query_position + 1 + 3 -
                                add_right] + '-' * add_right
                        else:

                            ref_sq = sequences[start + 1 - 3:start + 1 + 3].seq
                            read_sq = pread.alignment.query_sequence[
                                pread.query_position + 1 -
                                3:pread.query_position + 1 + pread.indel + 3]
                        gp = (pileupcol.pos, pileupcol.n, pread.indel, ref_sq,
                              read_sq)
                        if gp not in indel_pos_type_size:
                            indel_pos_type_size[gp] = 0
                        indel_pos_type_size[gp] += 1

                    continue

                if not pread.is_del and not pread.is_refskip:
                    # if (pread.query_position < endlen or
                    # (pread_len - pread.query_position + 1) < endlen):
                    # continue
                    tbase = pread.alignment.query_sequence[
                        pread.query_position]
                    if tbase not in bases:
                        bases[tbase] = {
                            "nuc_count": 0,
                            "codon_count": {},
                        }
                    bases[tbase][
                        "nuc_count"] += 1  # TODO: need to make correction based on mapped reason
                    add_left = 2 - add_left
                    add_left = add_left if add_left > 0 else 0
                    add_right = 3 - add_right
                    add_right = add_right if add_right > 0 else 0
                    add_left = '-' * add_left + pread.alignment.query_sequence[
                        pread.query_position -
                        (2 - add_left):pread.query_position]
                    add_right = pread.alignment.query_sequence[  # It is current + right
                        pread.query_position:pread.query_position +
                        (3 - add_right)] + '-' * add_right

                    seq_chunk = add_left + add_right
                    if seq_chunk not in bases[tbase]["codon_count"]:
                        bases[tbase]["codon_count"][seq_chunk] = 0
                    bases[tbase]["codon_count"][seq_chunk] += 1

                # NOTE: Deleting nucleotide which have low frequency
            if reads_to_remove_count:
                reads_to_remove[pileupcol.pos] = reads_to_remove_count

            break

        nucs_to_delete = ""
        for nuc in bases.keys():
            if nuc == sequences[start].seq:
                continue
            if bases[nuc]["nuc_count"] < alt_nuc_count * read_depth:
                nucs_to_delete += nuc
        for nuc in nucs_to_delete:
            del bases[nuc]
        if set(bases) - set([sequences[start].seq]):
            coordinates_with_change[start] = {
                "bases": bases,
                "read_count": read_depth,
            }
    tab = []
    for key, value in indel_pos_type_size.items():
        tab.append([key[0], key[1], key[2], key[3], key[4], value])
    indel_pos_type_size = pd.DataFrame(
        tab, columns=["coor", "depth", "indel", "ref", "read", "count"])

    # print(indel_pos_type_size)
    # indel_pos_type_size = indel_pos_type_size[indel_pos_type_size["indel"] %
    # 3 == 0]

    return coordinates_with_change, indel_pos_type_size, depth


def coor_with_changes_run(params, bam):
    params["sequences"] = pyfaidx.Fasta(params["ref"])[params["rid"]]
    params['sample'] = path.basename(bam).split('.bam')[0]
    merged_table_nuc = None
    res = changed_coordinates(params, bam)
    subs_table = sub_table(res[0], bam, params)
    indelframes = indel_frames(res[1], bam, params)
    merged_table = pd.concat([indelframes, subs_table[0]])

    res_indel = res[2]
    if len(res_indel):
        res_indel.loc[res_indel["indel"] < 0,
                      "seq"] = res_indel.loc[res_indel["indel"] < 0].apply(
                          lambda x: params["sequences"][x["coor"] + 1:x["coor"]
                                                        + 1 - x["indel"]].seq,
                          axis=1,
                      )  # NOTE: refefence nucleotide for deletion events
        res_indel["sample"] = flb
        res_indel["indel_read_pt"] = (res_indel["indel_read_count"] * 100.0 /
                                      res_indel["depth"])
    else:
        res_indel = pd.DataFrame()
    # NOTE: Done till here
    res_sub = res[0]
    res_table = {"pos": [], "read_count": [], "base_count": [], "base_pt": []}
    for pos in res_sub:
        res_table["pos"].append(pos)
        res_table["read_count"].append(res_sub[pos]["read_count"])
        base_count = []
        base_pt = []
        for base in res_sub[pos]["bases"]:
            base_count.append(
                f"{base}: {res_sub[pos]['bases'][base]['nuc_count']}")
            pt_val = "%.f" % (res_sub[pos]["bases"][base]["nuc_count"] *
                              100.0 / res_sub[pos]["read_count"])
            base_pt.append(f"{base}: {pt_val}")
        res_table["base_count"].append(",".join(base_count).replace(" ", ""))
        res_table["base_pt"].append(",".join(base_pt).replace(" ", ""))
    res_table = pd.DataFrame(res_table)
    if len(res_table):
        res_table["sample"] = flb
        res_table["ref_base"] = res_table.apply(
            lambda x: params["sequences"][x["pos"]].seq, axis=1)

    merged_table_nuc = [res_table, res_indel]
    return merged_table, merged_table_nuc, {flb: pd.DataFrame(res[-1])}
