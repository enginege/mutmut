#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import traceback
from io import (
    open,
)
from os.path import exists
from pathlib import Path
from shutil import copy
from time import time
from typing import List

import click
from glob2 import glob

from mutmut import (
    mutate_file,
    MUTANT_STATUSES,
    Context,
    __version__,
    mutations_by_type,
    mutmut_config,
    config_from_file,
    guess_paths_to_mutate,
    Config,
    Progress,
    check_coverage_data_filepaths,
    popen_streaming_output,
    run_mutation_tests,
    read_coverage_data,
    read_patch_data,
    add_mutations_by_file,
    python_source_files,
    compute_exit_code,
    print_status,
    close_active_queues,
)
from mutmut.cache import (
    create_html_report,
    cached_hash_of_tests,
)
from mutmut.cache import print_result_cache, print_result_ids_cache, \
    hash_of_tests, \
    filename_and_mutation_id_from_pk, cached_test_time, set_cached_test_time, \
    update_line_numbers, print_result_cache_junitxml, get_unified_diff


class MutationTestRunner:
    def __init__(self, config):
        self.config = config

    def run_baseline_tests(self):
        return time_test_suite(
            swallow_output=not self.config.swallow_output,
            test_command=self.config.test_command,
            using_testmon=self.config.using_testmon,
            current_hash_of_tests=self.config.hash_of_tests,
            no_progress=self.config.no_progress,
        )

    def generate_mutations(self, argument, dict_synonyms, paths_to_exclude, paths_to_mutate, tests_dirs):
        mutations_by_file = {}
        self.parse_run_argument(argument, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate,
                                tests_dirs)
        self.config.total = sum(len(mutations) for mutations in mutations_by_file.values())
        return mutations_by_file

    def run_mutation_tests(self, progress, mutations_by_file, max_workers):
        try:
            run_mutation_tests(config=self.config, progress=progress, mutations_by_file=mutations_by_file,
                               max_workers=max_workers)
        except Exception as e:
            traceback.print_exc()
            return compute_exit_code(progress, e)
        else:
            return compute_exit_code(progress, ci=self.config.ci)
        finally:
            print()  # make sure we end the output with a newline
            close_active_queues()

    def get_paths_to_mutate(self, paths_to_mutate):
        if paths_to_mutate is None:
            paths_to_mutate = guess_paths_to_mutate()

        if not isinstance(paths_to_mutate, (list, tuple)):
            paths_to_mutate = self.split_paths(paths_to_mutate)

        if not paths_to_mutate:
            raise click.BadOptionUsage(
                '--paths-to-mutate',
                'You must specify a list of paths to mutate.'
                'Either as a command line argument, or by setting paths_to_mutate under the section [mutmut] in setup.cfg.'
                'To specify multiple paths, separate them with commas or colons (i.e: --paths-to-mutate=path1/,path2/path3/,path4/).'
            )

        return paths_to_mutate

    def get_tests_dirs(self, tests_dir):
        test_paths = self.split_paths(tests_dir)
        if test_paths is None:
            raise FileNotFoundError(
                'No test folders found in current folder. Run this where there is a "tests" or "test" folder.'
            )
        return [p for p in test_paths for p in glob(p, recursive=True)]

    def get_covered_lines_by_filename(self, use_coverage, use_patch_file):
        if not use_coverage and not use_patch_file:
            return None

        covered_lines_by_filename = {}
        if use_coverage:
            coverage_data = read_coverage_data()
            check_coverage_data_filepaths(coverage_data)
        else:
            assert use_patch_file
            covered_lines_by_filename = read_patch_data(use_patch_file)

        return covered_lines_by_filename

    def get_mutation_types_to_apply(self, disable_mutation_types, enable_mutation_types):
        if enable_mutation_types:
            mutation_types_to_apply = set(mtype.strip() for mtype in enable_mutation_types.split(","))
            invalid_types = [mtype for mtype in mutation_types_to_apply if mtype not in mutations_by_type]
        elif disable_mutation_types:
            mutation_types_to_apply = set(mutations_by_type.keys()) - set(
                mtype.strip() for mtype in disable_mutation_types.split(","))
            invalid_types = [mtype for mtype in disable_mutation_types.split(",") if mtype not in mutations_by_type]
        else:
            mutation_types_to_apply = set(mutations_by_type.keys())
            invalid_types = None

        if invalid_types:
            raise click.BadArgumentUsage(
                f"The following are not valid mutation types: {', '.join(sorted(invalid_types))}. Valid mutation types are: {', '.join(mutations_by_type.keys())}")

        return mutation_types_to_apply

    def parse_run_argument(self, argument, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate,
                           tests_dirs):
        if argument is None:
            self.handle_no_argument(dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate, tests_dirs)
        else:
            self.handle_argument(argument, dict_synonyms, mutations_by_file)

    def handle_no_argument(self, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate, tests_dirs):
        for path in paths_to_mutate:
            self.process_files_in_path(path, tests_dirs, paths_to_exclude, dict_synonyms, mutations_by_file)

    def process_files_in_path(self, path, tests_dirs, paths_to_exclude, dict_synonyms, mutations_by_file):
        for filename in python_source_files(path, tests_dirs, paths_to_exclude):
            if not filename.startswith('test_') and not filename.endswith('__tests.py'):
                update_line_numbers(filename)
                add_mutations_by_file(mutations_by_file, filename, dict_synonyms, self.config)

    def handle_argument(self, argument, dict_synonyms, mutations_by_file):
        try:
            int(argument)  # to check if it's an integer
            filename, mutation_id = filename_and_mutation_id_from_pk(int(argument))
            update_line_numbers(filename)
            mutations_by_file[filename] = [mutation_id]
        except ValueError:
            if not os.path.exists(argument):
                raise click.BadArgumentUsage(
                    'The run command takes either an integer that is the mutation id or a path to a file to mutate')
            self.process_single_file(argument, dict_synonyms, mutations_by_file)

    def process_single_file(self, filename, dict_synonyms, mutations_by_file):
        update_line_numbers(filename)
        add_mutations_by_file(mutations_by_file, filename, dict_synonyms, self.config)

    @staticmethod
    def setup_environment():
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'  # stop python from creating .pyc files

    @staticmethod
    def validate_arguments(use_coverage, use_patch_file, disable_mutation_types, enable_mutation_types):
        if use_coverage and use_patch_file:
            raise click.BadArgumentUsage("You can't combine --use-coverage and --use-patch")

        if disable_mutation_types and enable_mutation_types:
            raise click.BadArgumentUsage("You can't combine --disable-mutation-types and --enable-mutation-types")

    @staticmethod
    def split_paths(paths):
        for sep in [',', ':']:
            separated = list(filter(lambda p: Path(p).exists(), paths.split(sep)))
            if separated:
                return separated
        return None

    @staticmethod
    def get_output_legend(simple_output):
        output_legend = {
            "killed": "🎉",
            "timeout": "⏰",
            "suspicious": "🤔",
            "survived": "🙁",
            "skipped": "🔇",
        }
        if simple_output:
            output_legend = {key: key.upper() for (key, value) in output_legend.items()}
        return output_legend


def do_apply(mutation_pk: str, dict_synonyms: List[str], backup: bool):
    """Apply a specified mutant to the source code

    :param mutation_pk: mutmut cache primary key of the mutant to apply
    :param dict_synonyms: list of synonym keywords for a python dictionary
    :param backup: if :obj:`True` create a backup of the source file
        before applying the mutation
    """
    filename, mutation_id = filename_and_mutation_id_from_pk(int(mutation_pk))

    update_line_numbers(filename)

    context = Context(
        mutation_id=mutation_id,
        filename=filename,
        dict_synonyms=dict_synonyms,
    )
    mutate_file(
        backup=backup,
        context=context,
    )


null_out = open(os.devnull, 'w')

DEFAULT_RUNNER = 'python -m pytest -x --assert=plain'


@click.group(context_settings=dict(help_option_names=['-h', '--help']))
def climain():
    """
    Mutation testing system for Python.

    Getting started:

    To run with pytest in test or tests folder: mutmut run

    For more options: mutmut run --help

    To show the results: mutmut results

    To generate HTML report: mutmut html
    """
    pass


@climain.command()
def version():
    """Show the version and exit."""
    print("mutmut version {}".format(__version__))
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('argument', nargs=1, required=False)
@click.option('--paths-to-mutate', type=click.STRING)
@click.option('--disable-mutation-types', type=click.STRING, help='Skip the given types of mutations.')
@click.option('--enable-mutation-types', type=click.STRING, help='Only perform given types of mutations.')
@click.option('--paths-to-exclude', type=click.STRING)
@click.option('--runner')
@click.option('--use-coverage', is_flag=True, default=False)
@click.option('--use-patch-file', help='Only mutate lines added/changed in the given patch file')
@click.option('--rerun-all', is_flag=True, default=False,
              help='If you modified the test_command in the pre_mutation hook, '
                   'the default test_command (specified by the "runner" option) '
                   'will be executed if the mutant survives with your modified test_command.')
@click.option('--tests-dir')
@click.option('-m', '--test-time-multiplier', default=2.0, type=float)
@click.option('-b', '--test-time-base', default=0.0, type=float)
@click.option('-s', '--swallow-output', help='turn off output capture', is_flag=True)
@click.option('--dict-synonyms')
@click.option('--pre-mutation')
@click.option('--post-mutation')
@click.option('--simple-output', is_flag=True, default=False,
              help="Swap emojis in mutmut output to plain text alternatives.")
@click.option('--no-progress', is_flag=True, default=False, help="Disable real-time progress indicator")
@click.option('--CI', is_flag=True, default=False,
              help="Returns an exit code of 0 for all successful runs and an exit code of 1 for fatal errors.")
@config_from_file(
    dict_synonyms='',
    paths_to_exclude='',
    runner=DEFAULT_RUNNER,
    tests_dir='tests/:test/',
    pre_mutation=None,
    post_mutation=None,
    use_patch_file=None,
)
@click.option('--max-workers', default=2, help='Set the max workers for ThreadPoolExecutor')
def run(argument, paths_to_mutate, disable_mutation_types, enable_mutation_types, runner,
        tests_dir, test_time_multiplier, test_time_base, swallow_output, use_coverage,
        dict_synonyms, pre_mutation, post_mutation, use_patch_file, paths_to_exclude,
        simple_output, no_progress, ci, rerun_all, max_workers):
    """
    Runs mutmut. You probably want to start with just trying this. If you supply a mutation ID mutmut will check just this mutant.

    Runs pytest by default (or unittest if pytest is unavailable) on tests in the “tests” or “test” folder.

    It is recommended to configure any non-default options needed in setup.cfg or pyproject.toml, as described in the documentation.

    Exit codes:

     * 0 - all mutants were killed

    Otherwise any or sum of any of the following exit codes:

     * 1 - if a fatal error occurred

     * 2 - if one or more mutants survived

     * 4 - if one or more mutants timed out

     * 8 - if one or more mutants caused tests to take twice as long

    (This is equivalent to a bit-OR combination of the exit codes that may apply.)

    With --CI flag enabled, the exit code will always be
    1 for a fatal error or 0 for any other case.
    """
    if test_time_base is None:  # click sets the default=0.0 to None
        test_time_base = 0.0
    if test_time_multiplier is None:  # click sets the default=0.0 to None
        test_time_multiplier = 0.0

    sys.exit(do_run(argument, paths_to_mutate, disable_mutation_types, enable_mutation_types, runner,
                    tests_dir, test_time_multiplier, test_time_base, swallow_output, use_coverage,
                    dict_synonyms, pre_mutation, post_mutation, use_patch_file, paths_to_exclude,
                    simple_output, no_progress, ci, rerun_all, max_workers))


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
def results():
    """
    Print the results.
    """
    print_result_cache()
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('status', nargs=1, required=True)
def result_ids(status):
    """
    Print the IDs of the specified mutant classes (separated by spaces).\n
    result-ids survived (or any other of: killed,timeout,suspicious,skipped,untested)\n
    """
    if not status or status not in MUTANT_STATUSES:
        raise click.BadArgumentUsage(f'The result-ids command needs a status class of mutants '
                                     f'(one of : {set(MUTANT_STATUSES.keys())}) but was {status}')
    print_result_ids_cache(status)
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('mutation-id', nargs=1, required=True)
@click.option('--backup/--no-backup', default=False)
@click.option('--dict-synonyms')
@config_from_file(
    dict_synonyms='',
)
def apply(mutation_id, backup, dict_synonyms):
    """
    Apply a mutation on disk.
    """
    do_apply(mutation_id, dict_synonyms, backup)
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('id-or-file', nargs=1, required=False)
@click.option('--dict-synonyms')
@config_from_file(
    dict_synonyms='',
)
def show(id_or_file, dict_synonyms):
    """
    Show a mutation diff.
    """
    if not id_or_file:
        print_result_cache()
        sys.exit(0)

    if id_or_file == 'all':
        print_result_cache(show_diffs=True, dict_synonyms=dict_synonyms)
        sys.exit(0)

    if os.path.isfile(id_or_file):
        print_result_cache(show_diffs=True, only_this_file=id_or_file)
        sys.exit(0)

    print(get_unified_diff(id_or_file, dict_synonyms))
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.option('--dict-synonyms')
@click.option('--suspicious-policy', type=click.Choice(['ignore', 'skipped', 'error', 'failure']), default='ignore')
@click.option('--untested-policy', type=click.Choice(['ignore', 'skipped', 'error', 'failure']), default='ignore')
@config_from_file(
    dict_synonyms='',
)
def junitxml(dict_synonyms, suspicious_policy, untested_policy):
    """
    Show a mutation diff with junitxml format.
    """
    print_result_cache_junitxml(dict_synonyms, suspicious_policy, untested_policy)
    sys.exit(0)


@climain.command(context_settings=dict(help_option_names=['-h', '--help']))
@click.option('--dict-synonyms')
@click.option('-d', '--directory', help='Write the output files to DIR.')
@config_from_file(
    dict_synonyms='',
    directory='html',
)
def html(dict_synonyms, directory):
    """
    Generate a HTML report of surviving mutants.
    """
    create_html_report(dict_synonyms, directory)
    sys.exit(0)


def do_run(
        argument,
        paths_to_mutate,
        disable_mutation_types,
        enable_mutation_types,
        runner,
        tests_dir,
        test_time_multiplier,
        test_time_base,
        swallow_output,
        use_coverage,
        dict_synonyms,
        pre_mutation,
        post_mutation,
        use_patch_file,
        paths_to_exclude,
        simple_output,
        no_progress,
        ci,
        rerun_all,
        max_workers,
) -> int:
    mutation_test_runner = MutationTestRunner(Config(
        total=0,
        swallow_output=not swallow_output,
        test_command=runner,
        covered_lines_by_filename=None,
        coverage_data={},
        baseline_time_elapsed=0.0,
        dict_synonyms=dict_synonyms,
        using_testmon='--testmon' in runner,
        tests_dirs=[],
        hash_of_tests='',
        test_time_multiplier=test_time_multiplier,
        test_time_base=test_time_base,
        pre_mutation=pre_mutation,
        post_mutation=post_mutation,
        paths_to_mutate=[],
        mutation_types_to_apply=set(),
        no_progress=no_progress,
        ci=ci,
        rerun_all=rerun_all
    ))

    mutation_test_runner.validate_arguments(use_coverage, use_patch_file, disable_mutation_types, enable_mutation_types)

    dict_synonyms = [x.strip() for x in dict_synonyms.split(',')]

    if use_coverage and not exists('.coverage'):
        raise FileNotFoundError('No .coverage file found. You must generate a coverage file to use this feature.')

    paths_to_mutate = mutation_test_runner.get_paths_to_mutate(paths_to_mutate)
    tests_dirs = mutation_test_runner.get_tests_dirs(tests_dir)

    current_hash_of_tests = hash_of_tests(tests_dirs)

    mutation_types_to_apply = mutation_test_runner.get_mutation_types_to_apply(disable_mutation_types,
                                                                               enable_mutation_types)

    covered_lines_by_filename = mutation_test_runner.get_covered_lines_by_filename(use_coverage, use_patch_file)
    coverage_data = read_coverage_data() if use_coverage else None

    mutation_test_runner.config.covered_lines_by_filename = covered_lines_by_filename
    mutation_test_runner.config.coverage_data = coverage_data
    mutation_test_runner.config.tests_dirs = tests_dirs
    mutation_test_runner.config.hash_of_tests = current_hash_of_tests
    mutation_test_runner.config.paths_to_mutate = paths_to_mutate
    mutation_test_runner.config.mutation_types_to_apply = mutation_types_to_apply

    mutation_test_runner.setup_environment()
    baseline_time_elapsed = mutation_test_runner.run_baseline_tests()
    mutation_test_runner.config.baseline_time_elapsed = baseline_time_elapsed

    mutations_by_file = mutation_test_runner.generate_mutations(argument, dict_synonyms, paths_to_exclude,
                                                                paths_to_mutate, tests_dirs)

    print()
    print('2. Checking mutants')
    progress = Progress(total=mutation_test_runner.config.total,
                        output_legend=mutation_test_runner.get_output_legend(simple_output), no_progress=no_progress)

    return mutation_test_runner.run_mutation_tests(progress, mutations_by_file, max_workers=max_workers)


def parse_run_argument(argument, config, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate,
                       tests_dirs):
    if argument is None:
        handle_no_argument(config, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate, tests_dirs)
    else:
        handle_argument(argument, config, dict_synonyms, mutations_by_file)


def handle_no_argument(config, dict_synonyms, mutations_by_file, paths_to_exclude, paths_to_mutate, tests_dirs):
    for path in paths_to_mutate:
        process_files_in_path(path, tests_dirs, paths_to_exclude, dict_synonyms, mutations_by_file, config)


def process_files_in_path(path, tests_dirs, paths_to_exclude, dict_synonyms, mutations_by_file, config):
    for filename in python_source_files(path, tests_dirs, paths_to_exclude):
        if not filename.startswith('test_') and not filename.endswith('__tests.py'):
            update_line_numbers(filename)
            add_mutations_by_file(mutations_by_file, filename, dict_synonyms, config)


def handle_argument(argument, config, dict_synonyms, mutations_by_file):
    try:
        int(argument)  # to check if it's an integer
        filename, mutation_id = filename_and_mutation_id_from_pk(int(argument))
        update_line_numbers(filename)
        mutations_by_file[filename] = [mutation_id]
    except ValueError:
        if not os.path.exists(argument):
            raise click.BadArgumentUsage(
                'The run command takes either an integer that is the mutation id or a path to a file to mutate')
        process_single_file(argument, dict_synonyms, mutations_by_file, config)


def process_single_file(filename, dict_synonyms, mutations_by_file, config):
    update_line_numbers(filename)
    add_mutations_by_file(mutations_by_file, filename, dict_synonyms, config)


def time_test_suite(
        swallow_output: bool,
        test_command: str,
        using_testmon: bool,
        current_hash_of_tests,
        no_progress,
) -> float:
    """Execute a test suite specified by ``test_command`` and record
    the time it took to execute the test suite as a floating point number

    :param swallow_output: if :obj:`True` test stdout will be not be printed
    :param test_command: command to spawn the testing subprocess
    :param using_testmon: if :obj:`True` the test return code evaluation will
        accommodate for ``pytest-testmon``
    :param current_hash_of_tests: the current hash of the tests
    :param no_progress: if :obj:`True` the progress indicator will be disabled

    :return: execution time of the test suite
    """
    cached_time = cached_test_time()
    if cached_time is not None and current_hash_of_tests == cached_hash_of_tests():
        print('1. Using cached time for baseline tests, to run baseline again delete the cache file')
        return cached_time

    print('1. Running tests without mutations')
    start_time = time()

    output = []

    def feedback(line):
        if not swallow_output:
            print(line)
        if not no_progress:
            print_status('Running...')
        output.append(line)

    returncode = popen_streaming_output(test_command, feedback)

    if returncode == 0 or (using_testmon and returncode == 5):
        baseline_time_elapsed = time() - start_time
    else:
        raise RuntimeError(
            "Tests don't run cleanly without mutations. Test command was: {}\n\nOutput:\n\n{}".format(test_command,
                                                                                                      '\n'.join(
                                                                                                          output)))

    print('Done')

    set_cached_test_time(baseline_time_elapsed, current_hash_of_tests)

    return baseline_time_elapsed


if __name__ == '__main__':
    climain()
