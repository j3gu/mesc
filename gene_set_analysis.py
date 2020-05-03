'''
Compute expression scores for gene sets. Expression scores can be meta-analyzed over multiple tissues/conditions or not.
'''

from __future__ import division
from collections import OrderedDict, Iterable, defaultdict
import numpy as np
import pandas as pd
import os
import argparse
import mesc.ldscore as ld
import mesc.parse as ps
import sys

dirname = os.path.dirname(__file__)

class Suppressor(object):
    '''
    Suppresses output from subprocess
    '''
    def __enter__(self):
        self.stdout = sys.stdout
        sys.stdout = self

    def __exit__(self, type, value, traceback):
        sys.stdout = self.stdout
        if type is not None:
            pass

    def write(self, x): pass

def read_file_line(fname):
    '''
    Read file with one item per line into list
    '''
    lines = []
    with open(fname, 'rb') as f:
        for l in f:
            l = l.strip()
            lines.append(l)
    return lines

def create_gset_expscore(args):
    '''
    Create gene set expression scores
    '''
    input_prefix = '{}.{}'.format(args.input_prefix, args.chr)
    gsets = read_gene_sets(args.gene_sets)

    lasso = pd.read_csv(input_prefix + '.lasso', sep='\t')
    h2cis = pd.read_csv(input_prefix + '.hsq', sep='\t')
    h2cis.dropna(inplace=True)

    keep_snps = pd.read_csv(args.keep, header=None)
    geno_fname = args.bfile
    bim = pd.read_csv(geno_fname + '.bim', sep='\t', header=None) # load bim
    bim = bim.loc[(bim[0] == args.chr).values & bim[1].isin(keep_snps[0]).values, 0:3]
    bim.columns = ['CHR', 'SNP', 'CM', 'BP']

    # keep genes with positive h2cis and converged LASSO
    snp_indices = dict(zip(bim['SNP'].tolist(), range(len(bim))))  # SNP indices for fast merging
    filtered_h2cis = h2cis[h2cis['h2cis'] > 0]  # filter out genes w/h2cis < 0
    filtered_h2cis = filtered_h2cis[~np.isnan(filtered_h2cis['h2cis'])]
    filtered_h2cis = filtered_h2cis[filtered_h2cis['Gene'].isin(lasso['GENE'])]
    if args.genes:
        keep_genes = read_file_line(args.genes)
        filtered_h2cis = filtered_h2cis[filtered_h2cis['Gene'].isin(keep_genes)]
    # filtered_h2cis determines final gene annot
    filtered_gene_indices = dict(zip(filtered_h2cis['Gene'].tolist(), range(len(filtered_h2cis))))

    # get gset names
    gset_names = ['Cis_herit_bin_{}'.format(x) for x in range(1,args.num_background_bins+1)]
    for k in gsets.keys():
        gset_names.extend(['{}_Cis_herit_bin_{}'.format(k, x) for x in range(1,args.num_gene_bins+1)])

    # create dict indicating gene membership in each gene set
    ave_h2cis = []  # compute average cis-heritability of genes in bin
    gene_gset_dict = defaultdict(list)
    # background gene set
    gene_bins = pd.qcut(filtered_h2cis['h2cis'], args.num_background_bins, labels=range(args.num_background_bins)).astype(int).tolist()
    temp_combined_herit = pd.DataFrame(np.c_[filtered_h2cis['h2cis'], gene_bins])
    temp_h2cis = temp_combined_herit.groupby([1]).mean()
    temp_h2cis = temp_h2cis[0].values
    ave_h2cis.extend(temp_h2cis)
    for i, gene in enumerate(filtered_h2cis['Gene']):
        gene_gset_dict[gene].append('Cis_herit_bin_{}'.format(gene_bins[i]+1))

    # remaining gene sets
    for k, v in gsets.items():
        temp_genes = [x for x in v if x in filtered_h2cis['Gene'].tolist()]
        temp_herit = filtered_h2cis.iloc[[filtered_gene_indices[x] for x in temp_genes], 2]
        gene_bins = pd.qcut(temp_herit, args.num_gene_bins, labels=range(args.num_gene_bins)).astype(int).tolist()
        temp_combined_herit = pd.DataFrame(np.c_[temp_herit.tolist(), gene_bins])
        temp_h2cis = temp_combined_herit.groupby([1]).mean()
        temp_h2cis = temp_h2cis[0].values
        ave_h2cis.extend(temp_h2cis)
        for i, gene in enumerate(temp_genes):
            gene_gset_dict[gene].append('{}_Cis_herit_bin_{}'.format(k, gene_bins[i]+1))
    gset_indices = dict(zip(gset_names, range(len(gset_names))))

    g_annot = []
    glist = []
    eqtl_annot = np.zeros((len(bim), len(gset_names)))

    # create eQTL annot (for expscore) and gene annot
    print('Combining eQTL weights')
    for i in range(len(filtered_h2cis)):
        gene = filtered_h2cis.iloc[i, 0]
        temp_h2cis = filtered_h2cis.iloc[i, 2]
        temp_lasso = lasso[lasso['GENE'] == gene]
        if len(temp_lasso) == 0:
            continue
        if gene not in gene_gset_dict.keys():
            g_annot.append(np.zeros(len(gset_names)))
        else:
            snp_idx = [snp_indices[x] for x in temp_lasso['SNP'].tolist()]
            temp_lasso_weights = temp_lasso['EFFECT'].values
            emp_herit = np.sum(np.square(temp_lasso_weights))
            if emp_herit <= 0:  # scale eQTL weights to h2cis
                bias = 0
            else:
                bias = np.sqrt(temp_h2cis / emp_herit)
            temp_lasso_weights *= bias
            temp_gset_indices = [gset_indices[x] for x in gene_gset_dict[gene]]
            for gset in temp_gset_indices:
                eqtl_annot[snp_idx, gset] += np.square(temp_lasso_weights)
            g_annot_toadd = np.zeros(len(gset_names))
            g_annot_toadd[temp_gset_indices] = 1
            g_annot.append(g_annot_toadd)
        glist.append(gene)

    g_annot = np.array(g_annot)
    g_annot_final = pd.DataFrame(np.c_[glist, g_annot])
    g_annot_final.columns = ['Gene'] + gset_names
    for i in range(1, g_annot_final.shape[1]):
        g_annot_final.iloc[:,i] = pd.to_numeric(g_annot_final.iloc[:,i]).astype(int)
    g_annot_final.to_csv('{}.{}.gannot.gz'.format(args.out, args.chr), sep='\t', index=False, compression='gzip')

    # output .G and .ave_h2cis files
    G = np.sum(g_annot, axis=0)
    np.savetxt('{}.{}.G'.format(args.out, args.chr), G.reshape((1, len(G))), fmt='%d')
    np.savetxt('{}.{}.ave_h2cis'.format(args.out, args.chr), np.array(ave_h2cis).reshape((1, len(ave_h2cis))),
               fmt="%.5f")

    print('Computing expression scores')
    # load genotypes
    array_indivs = ps.PlinkFAMFile(geno_fname + '.fam')
    array_snps = ps.PlinkBIMFile(geno_fname + '.bim')
    keep_snps_indices = np.where((array_snps.df['CHR'] == args.chr).values & array_snps.df['SNP'].isin(keep_snps[0]).values)[0]

    with Suppressor():
        geno_array = ld.PlinkBEDFile(geno_fname + '.bed', array_indivs.n, array_snps,
                                     keep_snps=keep_snps_indices)

    block_left = ld.getBlockLefts(geno_array.df[:, 2], 1e6)

    # estimate expression scores
    res = geno_array.ldScoreVarBlocks(block_left, c=50, annot=eqtl_annot)
    expscore = pd.DataFrame(np.c_[geno_array.df[:, :3], res])
    expscore.columns = geno_array.colnames[:3] + gset_names

    for name in gset_names:
        expscore[name] = expscore[name].astype(float)

    # output files
    expscore.to_csv('{}.{}.expscore.gz'.format(args.out, args.chr), sep='\t', index=False, compression='gzip',
                    float_format='%.5f')
    print('Done!')

def create_gset_expscore_meta(args):
    '''
    Create gene set expression scores meta-analyzed over several tissues/conditions
    '''
    input_prefixes = read_file_line(args.input_prefix_meta)
    input_prefixes_name = ['{}.{}'.format(x, args.chr) for x in input_prefixes]
    genes = get_gene_list(input_prefixes_name)
    gsets = read_gene_sets(args.gene_sets)

    # gene indices for fast merging
    gene_indices = dict(zip(genes, range(len(genes))))
    num = np.zeros(len(genes))
    count = np.zeros(len(genes))
    all_lasso = pd.DataFrame()

    # meta-analyze REML h2cis estimates by taking simple average
    # inverse-variance weighing has issues, since REML SE is downwardly biased for small h2 estimates
    for input in input_prefixes:
        cond = os.path.basename(input)
        lasso = pd.read_csv('{}.{}.lasso'.format(input, args.chr), sep='\t')
        lasso['COND'] = cond
        all_lasso = all_lasso.append(lasso)
        reml = pd.read_csv('{}.{}.hsq'.format(input, args.chr), sep='\t')
        reml.dropna(inplace=True)
        gene_idx = [gene_indices[x] for x in reml['Gene'].tolist()]
        num[gene_idx] += reml['h2cis'].values
        count[gene_idx] += 1

    count[count == 0] = np.nan
    meta_h2cis = num / count
    meta_h2cis_out = pd.DataFrame({'Gene': genes,
                                   'Chrom': args.chr,
                                   'h2cis': meta_h2cis})

    keep_snps = pd.read_csv(args.keep, header=None)
    geno_fname = args.bfile
    bim = pd.read_csv(geno_fname + '.bim', sep='\t', header=None) # load bim
    bim = bim.loc[(bim[0] == args.chr).values & bim[1].isin(keep_snps[0]).values, 0:3]
    bim.columns = ['CHR', 'SNP', 'CM', 'BP']

    # keep genes with positive h2cis and converged LASSO
    snp_indices = dict(zip(bim['SNP'].tolist(), range(len(bim))))  # SNP indices for fast merging
    filtered_meta_h2cis = meta_h2cis_out[meta_h2cis_out['h2cis'] > 0]  # filter out genes w/h2cis < 0
    filtered_meta_h2cis = filtered_meta_h2cis[~np.isnan(filtered_meta_h2cis['h2cis'])]
    filtered_meta_h2cis = filtered_meta_h2cis[filtered_meta_h2cis['Gene'].isin(all_lasso['GENE'])] # keep genes with converged LASSO
    if args.genes:
        keep_genes = read_file_line(args.genes)
        filtered_meta_h2cis = filtered_meta_h2cis[filtered_meta_h2cis['Gene'].isin(keep_genes)]
    # filtered_meta_h2cis determines final gene annot
    filtered_gene_indices = dict(zip(filtered_meta_h2cis['Gene'].tolist(), range(len(filtered_meta_h2cis))))

    # get gset names
    gset_names = ['Cis_herit_bin_{}'.format(x) for x in range(1,args.num_background_bins+1)]
    for k in gsets.keys():
        gset_names.extend(['{}_Cis_herit_bin_{}'.format(k, x) for x in range(1,args.num_gene_bins+1)])

    # create dict indicating gene membership in each gene set
    ave_h2cis = [] # compute average cis-heritability of genes in bin
    gene_gset_dict = defaultdict(list)

    # background gene set
    gene_bins = pd.qcut(filtered_meta_h2cis['h2cis'], args.num_background_bins,
                        labels=range(args.num_background_bins)).astype(int).tolist()
    temp_combined_herit = pd.DataFrame(np.c_[filtered_meta_h2cis['h2cis'], gene_bins])
    temp_h2cis = temp_combined_herit.groupby([1]).mean()
    temp_h2cis = temp_h2cis[0].values
    ave_h2cis.extend(temp_h2cis)
    for i, gene in enumerate(filtered_meta_h2cis['Gene']):
        gene_gset_dict[gene].append('Cis_herit_bin_{}'.format(gene_bins[i] + 1))

    for k, v in gsets.items():
        temp_genes = [x for x in v if x in filtered_meta_h2cis['Gene'].tolist()]
        temp_herit = filtered_meta_h2cis.iloc[[filtered_gene_indices[x] for x in temp_genes], 2]
        gene_bins = pd.qcut(temp_herit, args.num_gene_bins, labels=range(args.num_gene_bins)).astype(int).tolist()
        temp_combined_herit = pd.DataFrame(np.c_[temp_herit.tolist(), gene_bins])
        temp_h2cis = temp_combined_herit.groupby([1]).mean()
        temp_h2cis = temp_h2cis[0].values
        ave_h2cis.extend(temp_h2cis)
        for i, gene in enumerate(temp_genes):
            gene_gset_dict[gene].append('{}_Cis_herit_bin_{}'.format(k, gene_bins[i]+1))
    gset_indices = dict(zip(gset_names, range(len(gset_names))))

    g_annot = []
    g_annot_names = []
    eqtl_annot = np.zeros((len(bim), len(gset_names)))

    # create eQTL annot (for expscore) and gene annot
    print('Combining eQTL weights')
    for i in range(len(filtered_meta_h2cis)):
        gene = filtered_meta_h2cis.iloc[i, 1]
        temp_h2cis = filtered_meta_h2cis.iloc[i, 2]
        temp_lasso = all_lasso[all_lasso['GENE'] == gene]
        unique_conds = pd.unique(temp_lasso['COND'])
        if gene not in gene_gset_dict.keys():
            for temp_cond in unique_conds.tolist():
                g_annot.append(np.zeros(len(gset_names)))
                g_annot_names.append('{}_{}'.format(gene, temp_cond))
            continue

        for temp_cond in unique_conds:  # for each condition
            temp_temp_lasso = temp_lasso[temp_lasso['COND'] == temp_cond]
            snp_idx = [snp_indices[x] for x in temp_temp_lasso['SNP'].tolist()]
            temp_lasso_weights = temp_temp_lasso['EFFECT'].values
            emp_herit = np.sum(np.square(temp_lasso_weights))
            if emp_herit <= 0:  # scale eQTL weights to meta-tissue h2cis
                bias = 0
            else:
                bias = np.sqrt(temp_h2cis / emp_herit)
            temp_lasso_weights *= bias
            temp_gset_indices = [gset_indices[x] for x in gene_gset_dict[gene]]
            for gset in temp_gset_indices:
                eqtl_annot[snp_idx, gset] += np.square(temp_lasso_weights)
            g_annot_toadd = np.zeros(len(gset_names))
            g_annot_toadd[temp_gset_indices] = 1
            g_annot.append(g_annot_toadd)
            g_annot_names.append(gene + '_' + temp_cond)

    g_annot = np.array(g_annot)
    g_annot_final = pd.DataFrame(np.c_[g_annot_names, g_annot])
    g_annot_final.columns = ['Gene'] + gset_names
    for i in range(1, g_annot_final.shape[1]):
        g_annot_final.iloc[:,i] = pd.to_numeric(g_annot_final.iloc[:,i]).astype(int)
    g_annot_final.to_csv('{}.{}.gannot.gz'.format(args.out, args.chr), sep='\t', index=False, compression='gzip')

    # output .G and .ave_h2cis files
    G = np.sum(g_annot, axis=0)
    np.savetxt('{}.{}.G'.format(args.out, args.chr), G.reshape((1, len(G))), fmt='%d')
    np.savetxt('{}.{}.ave_h2cis'.format(args.out, args.chr), np.array(ave_h2cis).reshape((1, len(ave_h2cis))),
               fmt="%.5f")

    print('Computing expression scores')
    # load genotypes
    array_indivs = ps.PlinkFAMFile(geno_fname + '.fam')
    array_snps = ps.PlinkBIMFile(geno_fname + '.bim')
    keep_snps_indices = np.where((array_snps.df['CHR'] == args.chr).values & array_snps.df['SNP'].isin(keep_snps[0]).values)[0]

    with Suppressor():
        geno_array = ld.PlinkBEDFile(geno_fname + '.bed', array_indivs.n, array_snps,
                                     keep_snps=keep_snps_indices)

    block_left = ld.getBlockLefts(geno_array.df[:, 2], 1e6)

    # estimate expression scores
    res = geno_array.ldScoreVarBlocks(block_left, c=50, annot=eqtl_annot)
    expscore = pd.DataFrame(np.c_[geno_array.df[:, :3], res])
    expscore.columns = geno_array.colnames[:3] + gset_names

    for name in gset_names:
        expscore[name] = expscore[name].astype(float)

    # output files
    expscore.to_csv('{}.{}.expscore.gz'.format(args.out, args.chr), sep='\t', index=False, compression='gzip',
                    float_format='%.5f')
    print('Done!')

def read_gene_sets(fname):
    '''
    Read gene sets from file.
    '''
    gsets = OrderedDict()
    with open(fname, 'rb') as f:
        for line in f:
            line = line.strip().split()
            gsets[line[0]] = line[1:]
    return gsets

def get_gene_list(input_prefixes):
    '''
    Get union of all genes found in all input files
    '''
    genes = []
    for file in input_prefixes:
        fname = file + '.hsq'
        with open(fname, 'rb') as f:
            next(f)
            for line in f:
                genes.append(line.split()[0])
    genes = list(set(genes))
    return genes

def flatten(items):
    '''
    Flatten nested list
    '''
    for x in items:
        if isinstance(x, Iterable) and not isinstance(x, (str, bytes)):
            for sub_x in flatten(x):
                yield sub_x
        else:
            yield x

parser = argparse.ArgumentParser()
parser.add_argument('--out', default=None, type=str,
                    help='Output filename prefix.')
parser.add_argument('--input-prefix', default=None, type=str,
                    help='File prefix for .lasso and .hsq files.')
parser.add_argument('--input-prefix-meta', default=None, type=str,
                    help='File containing list of file prefixes for .lasso and .hsq files to meta-analyze over.'
                         'One name per line.')
parser.add_argument('--gene-sets', default=None, type=str,
                    help='File containing gene sets. One gene set per line. First column is gene set name, remaining '
                         'columns are gene names.')
parser.add_argument('--bfile', default=None, type=str,
                    help='Genotypes used to compute expression scores, which should be ancestry-matched to GWAS samples.'
                         'We recommend using 1000 Genomes.')
parser.add_argument('--chr', default=None, type=int,
                    help='Chromosome number.')
parser.add_argument('--keep', default=os.path.join(dirname, 'data/hm3_snps.txt'), type=str,
                    help='File with SNPs to include in expression score estimation. '
                         'The file should contain one SNP ID per row.')
parser.add_argument('--num-gene-bins', default=3, type=int,
                    help='Number of bins to split each gene set into. Default 3.')
parser.add_argument('--num-background-bins', default=5, type=int,
                    help='Number of bins to split background set of all genes into. Default 5.')
parser.add_argument('--genes', default=None, type=str,
                    help='File containing set of background genes to retain in analysis. h2med enrichment is computed '
                         'relative to background set of genes. One name per line')

if __name__ == '__main__':

    args = parser.parse_args()
    if args.out is None:
        raise ValueError('Must specify --out')
    if not args.make_kb_window:
        if not (args.input_prefix or args.input_prefix_meta):
            raise ValueError('Must specify --input-prefix or --input-prefix-meta')
        if (args.input_prefix and args.input_prefix_meta):
            raise ValueError('Cannot specify both --input-prefix and --input-prefix-meta')
    if args.bfile is None:
        raise ValueError('Must specify --bfile')
    if args.chr is None:
        raise ValueError('Must specify --chr')
    if args.gene_sets is None:
        raise ValueError('Must specify --gene-sets')
    if args.input_prefix_meta:
        create_gset_expscore_meta(args)
    elif args.input_prefix:
        create_gset_expscore(args)
