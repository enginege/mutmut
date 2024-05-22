import os
import traceback
from pathlib import Path
from time import time
import click
from glob2 import glob
from mutmut import run_mutation_tests, compute_exit_code, close_active_queues, guess_paths_to_mutate, \
    python_source_files, add_mutations_by_file, mutations_by_type, read_coverage_data, check_coverage_data_filepaths, \
    read_patch_data, popen_streaming_output, print_status
from mutmut.cache import update_line_numbers, filename_and_mutation_id_from_pk, cached_test_time, cached_hash_of_tests, set_cached_test_time


class MutationTestRunner:
    def __init__(self, config):
        self.config = config

    def run_baseline_tests(self):
        return self.time_test_suite(
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

    @staticmethod
    def get_mutation_types_to_apply(disable_mutation_types, enable_mutation_types):
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

    @staticmethod
    def get_covered_lines_by_filename(use_coverage, use_patch_file):
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

    @staticmethod
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
