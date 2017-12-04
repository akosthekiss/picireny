# Copyright (c) 2016-2018 Renata Hodovan, Akos Kiss.
#
# Licensed under the BSD 3-Clause License
# <LICENSE.rst or https://opensource.org/licenses/BSD-3-Clause>.
# This file may not be copied, modified, or distributed except
# according to those terms.

import antlerinator
import codecs
import json
import logging
import picire
import pkgutil
import sys

from argparse import ArgumentParser
from os import makedirs
from os.path import abspath, basename, dirname, exists, join, relpath
from shutil import rmtree

from antlr4 import *
from . import info, transform
from .antlr4 import create_hdd_tree
from .hdd import coarse_full_hddmin, coarse_hddmin, hddmin

logger = logging.getLogger('picireny')
__version__ = pkgutil.get_data(__package__, 'VERSION').decode('ascii').strip()
antlr_default_path = antlerinator.antlr_jar_path


args_hdd_choices = {
    'full': hddmin,
    'coarse': coarse_hddmin,
    'coarse-full': coarse_full_hddmin,
}


def process_antlr_path(antlr):
    if antlr == antlr_default_path:
        antlerinator.install(lazy=True)

    if not exists(antlr):
        logger.error('%s does not exist.' % antlr)
        return None

    return abspath(relpath(antlr))


def process_antlr_format(*, format=None, grammar=None, start=None, replacements=None):
    def load_format_config(data):
        # Interpret relative grammar paths compared to the directory of the config file.
        if 'files' in data:
            for i, fn in enumerate(data['files']):
                path = join(abspath(dirname(format)), fn)
                if not exists(path):
                    logger.error('{path}, defined in the format config, doesn\'t exist.'.format(path=path))
                    return None, None
                data['files'][i] = path
            data['islands'] = data.get('islands', {})
            data['replacements'] = data.get('replacements', {})
        return data

    input_format = dict()

    if format:
        if not exists(format):
            logger.error('{path} does not exist.'.format(path=format))
            return None, None

        with open(format, 'r') as f:
            try:
                input_description = json.load(f, object_hook=load_format_config)
                input_format = input_description['grammars']
                if not start:
                    start = input_description.get('start', None)
            except json.JSONDecodeError as err:
                logger.error('The content of {path} is not a valid JSON object: {err}'.format(path=format, err=err))
                return None, None

    if not start:
        logger.error('No start has been defined.')
        return None, None

    if grammar or replacements:
        # Initialize the default grammar that doesn't need to be named.
        input_format[''] = input_format.get('', {'files': [], 'replacements': {}, 'islands': {}})

        if grammar:
            for i, g in enumerate(grammar):
                input_format['']['files'].append(abspath(relpath(g)))
                if not exists(input_format['']['files'][i]):
                    logger.error('{path} does not exist.'.format(path=input_format['']['files'][i]))
                    return None, None

        if replacements:
            if not exists(replacements):
                logger.error('{path} does not exist.'.format(path=replacements))
                return None, None

            try:
                with open(replacements, 'r') as f:
                    input_format['']['replacements'] = json.load(f)
            except json.JSONDecodeError as err:
                logger.error('The content of {path} is not a valid JSON object: {err}'.format(path=replacements, err=err))
                return None, None

    return input_format, start


def process_antlr_args(arg_parser, args):
    args.antlr = process_antlr_path(args.antlr)
    if args.antlr is None:
        arg_parser.error('Invalid ANTLR definition.')

    args.input_format, args.start = process_antlr_format(format=args.format, grammar=args.grammar, start=args.start,
                                                         replacements=args.replacements)
    if args.input_format is None or args.start is None:
        arg_parser.error('Invalid input format definition.')


def process_args(arg_parser, args):
    args.hddmin = args_hdd_choices[args.hdd]
    process_antlr_args(arg_parser, args)
    picire.cli.process_args(arg_parser, args)


def log_args(title, args):
    def _log_args(args):
        if not args:
            return repr(args)
        if isinstance(args, dict):
            log = []
            for k, v in sorted(args.items()):
                k_log = _log_args(k)
                v_log = _log_args(v)
                if isinstance(v_log, list):
                    log += ['%s:' % k_log]
                    for i, line in enumerate(v_log):
                        log += ['\t' + line]
                else:
                    log += ['%s: %s' % (k_log, v_log)]
            return log if len(log) > 1 else log[0]
        if isinstance(args, list):
            return ', '.join(_log_args(v) for v in args)
        if hasattr(args, '__name__'):
            return '.'.join(([args.__module__] if hasattr(args, '__module__') else []) + [args.__name__])
        return args
    logger.info('%s\n\t%s\n', title, '\n\t'.join(_log_args(args)))


def log_tree(title, hdd_tree):
    logger.debug('%s\n\theight: %s\n\tshape: %s\n\tnodes: %s\n',
                 title,
                 info.height(hdd_tree),
                 ', '.join(['%s' % cnt for cnt in info.shape(hdd_tree)]),
                 ', '.join(['%d %s' % (cnt, ty) for ty, cnt in sorted(info.count(hdd_tree).items())]))


def call(*,
         reduce_class, reduce_config,
         tester_class, tester_config,
         input, src, encoding, out,
         hddmin,
         antlr, input_format, start, build_hidden_tokens=False, lang='python',
         hdd_star=True, squeeze_tree=True, skip_unremovable=True,
         skip_whitespace=False,
         flatten_recursion=False, granularity=2,
         cache_class=None, cleanup=True):
    """
    Execute picireny as if invoked from command line, however, control its
    behaviour not via command line arguments but function parameters.

    :param reduce_class: Reference to the reducer class.
    :param reduce_config: Dictionary containing information to initialize the reduce_class.
    :param tester_class: Reference to a runnable class that can decide about the interestingness of a test case.
    :param tester_config: Dictionary containing information to initialize the tester_class.
    :param input: Path to the test case to reduce (only used to determine the name of the output file).
    :param src: Contents of the test case to reduce.
    :param encoding: Encoding of the input test case.
    :param out: Path to the output directory.
    :param hddmin: Function implementing a HDD minimization algorithm.
    :param antlr: Path to the ANTLR4 tool (Java jar binary).
    :param input_format: Dictionary describing the input format.
    :param start: Name of the start rule in [grammarname:]rulename format.
    :param build_hidden_tokens: Build hidden tokens of the input format into the HDD tree.
    :param lang: The target language of the parser.
    :param hdd_star: Boolean to enable the HDD star algorithm.
    :param squeeze_tree: Boolean to enable the tree squeezing optimization.
    :param skip_unremovable: Boolean to enable hiding unremovable nodes from ddmin.
    :param skip_whitespace: Boolean to enable hiding whitespace-only tokens from ddmin.
    :param flatten_recursion: Boolean to enable flattening left/right-recursive trees.
    :param granularity: Initial granularity.
    :param cache_class: Reference to the cache class to use.
    :param cleanup: Binary flag denoting whether removing auxiliary files at the end is enabled.
    :return: The path to the minimal test case.
    """

    # Get the parameters in a dictionary so that they can be pretty-printed
    # (minus src, as that parameter can be arbitrarily large)
    args = locals().copy()
    del args['src']
    log_args('Reduce session starts for %s' % input, args)

    grammar_workdir = join(out, 'grammar')
    makedirs(grammar_workdir, exist_ok=True)
    hdd_tree = create_hdd_tree(InputStream(src.decode(encoding)), input_format, start, antlr, grammar_workdir,
                               hidden_tokens=build_hidden_tokens, lang=lang)
    log_tree('Initial tree', hdd_tree)

    if flatten_recursion:
        hdd_tree = transform.flatten_recursion(hdd_tree)
        log_tree('Tree after recursion flattening', hdd_tree)

    if squeeze_tree:
        hdd_tree = transform.squeeze_tree(hdd_tree)
        log_tree('Tree after squeezing', hdd_tree)

    if skip_unremovable:
        hdd_tree = transform.skip_unremovable(hdd_tree, unparse_with_whitespace=not build_hidden_tokens)
        log_tree('Tree after skipping unremovable nodes', hdd_tree)

    if skip_whitespace:
        hdd_tree = transform.skip_whitespace(hdd_tree)
        log_tree('Tree after skipping whitespace tokens', hdd_tree)

    # Start reduce and save result to a file named the same like the original.
    out_file = join(out, basename(input))
    tests_workdir = join(out, 'tests')
    makedirs(tests_workdir, exist_ok=True)
    with codecs.open(out_file, 'w', encoding=encoding, errors='ignore') as f:
        f.write(hddmin(hdd_tree,
                       reduce_class,
                       reduce_config,
                       tester_class,
                       tester_config,
                       basename(input),
                       tests_workdir,
                       hdd_star=hdd_star,
                       cache=cache_class() if cache_class else None,
                       unparse_with_whitespace=not build_hidden_tokens,
                       granularity=granularity))
    logger.info('Result is saved to %s.', out_file)

    if cleanup:
        rmtree(grammar_workdir)
        rmtree(tests_workdir)

    return out_file


def execute():
    """
    The main entry point of picireny.
    """

    arg_parser = ArgumentParser(description='CLI for the Picireny Hierarchical Delta Debugging Framework',
                                parents=[picire.cli.create_parser()], add_help=False)

    # Grammar specific settings.
    arg_parser.add_argument('--hdd', metavar='NAME', choices=args_hdd_choices.keys(), default='full',
                            help='HDD variant to run (%(choices)s; default: %(default)s)')
    arg_parser.add_argument('-s', '--start', metavar='NAME',
                            help='name of the start rule in [grammarname:]rulename format (default for '
                                 'the optional grammarname is the empty string)')
    arg_parser.add_argument('-g', '--grammar', metavar='FILE', nargs='+',
                            help='grammar file(s) describing the input format (these grammars will be '
                                 'associated with the empty grammar name, see `--start`)')
    arg_parser.add_argument('-r', '--replacements', metavar='FILE',
                            help='JSON file defining the default replacements for lexer and parser '
                                 'rules of the grammar with the empty name (usually defined via `--grammar`)')
    arg_parser.add_argument('--antlr', metavar='FILE', default=antlr_default_path,
                            help='path where the antlr jar file is installed (default: %(default)s)')
    arg_parser.add_argument('--format', metavar='FILE',
                            help='JSON file describing a (possibly complex) input format')
    arg_parser.add_argument('--build-hidden-tokens', default=False, action='store_true',
                            help='build hidden tokens of the grammar(s) into the HDD tree')
    arg_parser.add_argument('--parser', metavar='LANG', default='python', choices=['python', 'java'],
                            help='language of the generated parsers (%(choices)s; default: %(default)s) '
                                 '(using Java might gain performance, but needs JDK)')
    arg_parser.add_argument('--no-hdd-star', dest='hdd_star', default=True, action='store_false',
                            help='run the hddmin algorithm only once')
    arg_parser.add_argument('--no-squeeze-tree', dest='squeeze_tree', default=True, action='store_false',
                            help='don\'t squeeze rule chains in tree representation')
    arg_parser.add_argument('--no-skip-unremovable', dest='skip_unremovable', default=True, action='store_false',
                            help='don\'t hide unremovable nodes from the ddmin algorithm')
    arg_parser.add_argument('--skip-whitespace', dest='skip_whitespace', default=False, action='store_true',
                            help='hide whitespace tokens from the ddmin algorithm')
    arg_parser.add_argument('--flatten-recursion', default=False, action='store_true',
                            help='flatten recurring blocks of left/right-recursive rules')
    arg_parser.add_argument('--sys-recursion-limit', metavar='NUM', type=int,
                            help='override maximum depth of the Python interpreter stack (may be needed for --parser=java)')
    arg_parser.add_argument('--version', action='version', version='%(prog)s {version}'.format(version=__version__))

    args = arg_parser.parse_args()
    process_args(arg_parser, args)

    logging.basicConfig(format='%(message)s')
    logger.setLevel(args.log_level)
    logging.getLogger('picire').setLevel(logger.level)

    if args.sys_recursion_limit:
        sys.setrecursionlimit(args.sys_recursion_limit)

    call(reduce_class=args.reduce_class,
         reduce_config=args.reduce_config,
         tester_class=args.tester_class,
         tester_config=args.tester_config,
         input=args.input,
         src=args.src,
         encoding=args.encoding,
         out=args.out,
         hddmin=args.hddmin,
         antlr=args.antlr,
         input_format=args.input_format,
         start=args.start,
         build_hidden_tokens=args.build_hidden_tokens,
         lang=args.parser,
         hdd_star=args.hdd_star,
         squeeze_tree=args.squeeze_tree,
         skip_unremovable=args.skip_unremovable,
         skip_whitespace=args.skip_whitespace,
         flatten_recursion=args.flatten_recursion,
         granularity=args.granularity,
         cache_class=args.cache,
         cleanup=args.cleanup)
