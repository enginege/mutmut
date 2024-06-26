# -*- coding: utf-8 -*-
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import uuid
import fnmatch
import multiprocessing
import os
import shlex
import subprocess
import sys
import toml
from configparser import ConfigParser
from copy import copy as copy_obj
from functools import wraps
from io import (
    open,
    TextIOBase,
)
from os.path import isdir
from shutil import (
    move,
    copy,
)
from threading import (
    Timer,
    Thread,
)
from time import time
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Union

from parso import parse

from .utils import (
    RelativeMutationID, ALL,
    ASTPattern, import_from_star_pattern, array_subscript_pattern, function_call_pattern,
    Context,
    Config,
    Progress, UNTESTED, SKIPPED, BAD_TIMEOUT, OK_SUSPICIOUS, BAD_SURVIVED, OK_KILLED, MUTANT_STATUSES, print_status,
    SkipException,
    MutationCollection
)

from .mutation_operations import (
    MutationStrategy,
    NumberMutation,
    StringMutation,
    FStringMutation,
    LambdaMutation,
    ArgumentMutation,
    KeywordMutation,
    OperatorMutation,
    AndOrTestMutation,
    ExpressionMutation,
    DecoratorMutation,
    NameMutation,
)

__version__ = '2.4.5'

if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())
try:
    import mutmut_config
except ImportError:
    mutmut_config = None

# We have a global whitelist for constants of the pattern __all__, __version__, etc

dunder_whitelist = [
    'all',
    'version',
    'title',
    'package_name',
    'author',
    'description',
    'email',
    'version',
    'license',
    'copyright',
]


def partition_node_list(nodes, value):
    for i, n in enumerate(nodes):
        if hasattr(n, 'value') and n.value == value:
            return nodes[:i], n, nodes[i + 1:]

    assert False, "didn't find node to split on"


mutations_by_type = {
    'operator': OperatorMutation(),
    'keyword': KeywordMutation(),
    'number': NumberMutation(),
    'name': NameMutation(),
    'string': StringMutation(),
    'fstring': FStringMutation(),
    'argument': ArgumentMutation(),
    'or_test': AndOrTestMutation(),
    'and_test': AndOrTestMutation(),
    'lambdef': LambdaMutation(),
    'expr_stmt': ExpressionMutation(),
    'decorator': DecoratorMutation(),
    'annassign': ExpressionMutation(),
}


# TODO: detect regexes and mutate them in nasty ways? Maybe mutate all strings as if they are regexes

def mutate(context: Context) -> Tuple[str, int]:
    try:
        result = parse(context.source, error_recovery=False)
    except Exception:
        print('Failed to parse {}. Internal error from parso follows.'.format(context.filename))
        print('----------------------------------')
        raise
    mutation_collection = MutationCollection(result.children)
    mutation_iterator = mutation_collection.get_iterator()
    while mutation_iterator.has_next():
        node = mutation_iterator.current()
        mutate_node(node, context=context)
        mutation_iterator.__next__()
    mutated_source = result.get_code().replace(' not not ', ' ')
    if context.remove_newline_at_end:
        assert mutated_source[-1] == '\n'
        mutated_source = mutated_source[:-1]

    # If we said we mutated the code, check that it has actually changed
    if context.performed_mutation_ids:
        if context.source == mutated_source:
            raise RuntimeError(
                "Mutation context states that a mutation occurred but the "
                "mutated source remains the same as original")
    context.mutated_source = mutated_source
    return mutated_source, len(context.performed_mutation_ids)


def mutate_node(node, context: Context):
    context.stack.append(node)
    try:
        if node.type in ('tfpdef', 'import_from', 'import_name'):
            return

        if node.type == 'atom_expr' and node.children and node.children[0].type == 'name' and node.children[0].value == '__import__':
            return

        if node.start_pos[0] - 1 != context.current_line_index:
            context.current_line_index = node.start_pos[0] - 1
            context.index = 0  # indexes are unique per line, so start over here!

        if node.type == 'expr_stmt':
            if node.children[0].type == 'name' and node.children[0].value.startswith('__') and node.children[0].value.endswith('__'):
                if node.children[0].value[2:-2] in dunder_whitelist:
                    return

        # Avoid mutating pure annotations
        if node.type == 'annassign' and len(node.children) == 2:
            return

        if hasattr(node, 'children'):
            mutate_list_of_nodes(node, context=context)

            # this is just an optimization to stop early
            if context.performed_mutation_ids and context.mutation_id != ALL:
                return

        mutation_strategy = mutations_by_type.get(node.type)

        if mutation_strategy is None:
            return

        old = getattr(node, 'value', None) or getattr(node, 'children', None)
        if context.exclude_line():
            return

        new = mutation_strategy.mutate(
            context=context,
            node=node,
            value=getattr(node, 'value', None),
            children=getattr(node, 'children', None),
        )

        if isinstance(new, list) and not isinstance(old, list):
            # multiple mutations
            new_list = new
        else:
            # one mutation
            new_list = [new]

        # go through the alternate mutations in reverse as they may have
        # adverse effects on subsequent mutations, this ensures the last
        # mutation applied is the original/default/legacy mutmut mutation
        for new in reversed(new_list):
            assert not callable(new)
            if new is not None and new != old:
                if hasattr(mutmut_config, 'pre_mutation_ast'):
                    mutmut_config.pre_mutation_ast(context=context)
                if context.should_mutate(node):
                    context.performed_mutation_ids.append(context.mutation_id_of_current_index)
                    if hasattr(node, 'value'):
                        node.value = new
                    else:
                        node.children = new
                context.index += 1
            # this is just an optimization to stop early
            if context.performed_mutation_ids and context.mutation_id != ALL:
                return
    finally:
        context.stack.pop()


def mutate_list_of_nodes(node, context: Context):
    return_annotation_started = False

    for child_node in node.children:
        if child_node.type == 'operator' and child_node.value == '->':
            return_annotation_started = True

        if return_annotation_started and child_node.type == 'operator' and child_node.value == ':':
            return_annotation_started = False

        if return_annotation_started:
            continue

        mutate_node(child_node, context=context)

        # this is just an optimization to stop early
        if context.performed_mutation_ids and context.mutation_id != ALL:
            return


def list_mutations(context: Context):
    assert context.mutation_id == ALL
    mutate(context)
    return context.performed_mutation_ids


def mutate_file(backup: bool, context: Context) -> Tuple[str, str]:
    with open(context.filename) as f:
        original = f.read()
    if backup:
        context.backup_filename = f"{context.filename}.{uuid.uuid4()}.bak"
        with open(context.backup_filename, 'w') as f:
            f.write(original)
    mutated, _ = mutate(context)
    with open(context.filename, 'w') as f:
        f.write(mutated)
    return original, mutated


def queue_mutants(
        *,
        progress: Progress,
        config: Config,
        mutants_queue,
        mutations_by_file: Dict[str, List[RelativeMutationID]],
        max_workers: int = 2
):
    from mutmut.cache import get_cached_mutation_statuses

    try:
        index = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for filename, mutations in mutations_by_file.items():
                cached_mutation_statuses = get_cached_mutation_statuses(filename, mutations, config.hash_of_tests)
                with open(filename) as f:
                    source = f.read()
                for mutation_id in mutations:
                    cached_status = cached_mutation_statuses.get(mutation_id)
                    if cached_status != UNTESTED:
                        progress.register(cached_status)
                        continue
                    context = Context(
                        mutation_id=mutation_id,
                        filename=filename,
                        dict_synonyms=config.dict_synonyms,
                        config=copy_obj(config),
                        source=source,
                        index=index,
                    )
                    futures.append(executor.submit(mutants_queue.put, ('mutant', context)))
                    index += 1
            for future in futures:
                future.result()
    finally:
        mutants_queue.put(('end', None))


def check_mutants(mutants_queue, results_queue, cycle_process_after, max_workers):
    def feedback(line):
        results_queue.put(('progress', line, None, None))

    did_cycle = False

    try:
        count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            while True:
                command, context = mutants_queue.get()
                if command == 'end':
                    break

                future = executor.submit(run_mutation, context, feedback)
                futures.append((future, context))
                count += 1
                if count == cycle_process_after:
                    results_queue.put(('cycle', None, None, None))
                    did_cycle = True
                    break

            for future, context in futures:
                status = future.result()
                results_queue.put(('status', status, context.filename, context.mutation_id))
    finally:
        if not did_cycle:
            results_queue.put(('end', None, None, None))


def run_mutation(context: Context, callback) -> str:
    """
    :return: (computed or cached) status of the tested mutant, one of mutant_statuses
    """
    from mutmut.cache import cached_mutation_status
    cached_status = cached_mutation_status(context.filename, context.mutation_id, context.config.hash_of_tests)

    if cached_status != UNTESTED and context.config.total != 1:
        return cached_status

    config = context.config
    if hasattr(mutmut_config, 'pre_mutation'):
        context.current_line_index = context.mutation_id.line_number
        try:
            mutmut_config.pre_mutation(context=context)
        except SkipException:
            return SKIPPED
        if context.skip:
            return SKIPPED

    if config.pre_mutation:
        result = subprocess.check_output(config.pre_mutation, shell=True).decode().strip()
        if result and not config.swallow_output:
            callback(result)

    try:
        mutate_file(
            backup=True,
            context=context
        )
        start = time()
        try:
            survived = tests_pass(config=config, callback=callback)
            if survived and config.test_command != config.default_test_command and config.rerun_all:
                # rerun the whole test suite to be sure the mutant can not be killed by other tests
                config.test_command = config.default_test_command
                survived = tests_pass(config=config, callback=callback)
        except TimeoutError:
            return BAD_TIMEOUT

        time_elapsed = time() - start
        if not survived and time_elapsed > config.test_time_base + (
                config.baseline_time_elapsed * config.test_time_multiplier
        ):
            return OK_SUSPICIOUS

        if survived:
            return BAD_SURVIVED
        else:
            return OK_KILLED
    except SkipException:
        return SKIPPED

    finally:
        move(context.backup_filename, context.filename)
        config.test_command = config.default_test_command  # reset test command to its default in the case it was altered in a hook

        if config.post_mutation:
            result = subprocess.check_output(config.post_mutation, shell=True).decode().strip()
            if result and not config.swallow_output:
                callback(result)


def tests_pass(config: Config, callback) -> bool:
    """
    :return: :obj:`True` if the tests pass, otherwise :obj:`False`
    """
    if config.using_testmon:
        copy('.testmondata-initial', '.testmondata')

    use_special_case = True

    # Special case for hammett! We can do in-process test running which is much faster
    if use_special_case and config.test_command.startswith(hammett_prefix):
        return hammett_tests_pass(config, callback)

    returncode = popen_streaming_output(config.test_command, callback, timeout=config.baseline_time_elapsed * 10)
    return returncode not in (1, 2)


def config_from_file(**defaults):
    def config_from_pyproject_toml() -> dict:
        try:
            return toml.load('pyproject.toml')['tool']['mutmut']
        except (FileNotFoundError, KeyError):
            return {}

    def config_from_setup_cfg() -> dict:
        config_parser = ConfigParser()
        config_parser.read('setup.cfg')

        try:
            return dict(config_parser['mutmut'])
        except KeyError:
            return {}

    config = config_from_pyproject_toml() or config_from_setup_cfg()

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            for k in list(kwargs.keys()):
                if not kwargs[k]:
                    kwargs[k] = config.get(k, defaults.get(k))
            f(*args, **kwargs)

        return wrapper

    return decorator


def guess_paths_to_mutate() -> str:
    """Guess the path to source code to mutate"""
    this_dir = os.getcwd().split(os.sep)[-1]
    if isdir('lib'):
        return 'lib'
    elif isdir('src'):
        return 'src'
    elif isdir(this_dir):
        return this_dir
    elif isdir(this_dir.replace('-', '_')):
        return this_dir.replace('-', '_')
    elif isdir(this_dir.replace(' ', '_')):
        return this_dir.replace(' ', '_')
    elif isdir(this_dir.replace('-', '')):
        return this_dir.replace('-', '')
    elif isdir(this_dir.replace(' ', '')):
        return this_dir.replace(' ', '')
    raise FileNotFoundError(
        'Could not figure out where the code to mutate is. '
        'Please specify it on the command line using --paths-to-mutate, '
        'or by adding "paths_to_mutate=code_dir" in pyproject.toml or setup.cfg to the [mutmut] '
        'section.')


def check_coverage_data_filepaths(coverage_data):
    for filepath in coverage_data:
        if not os.path.exists(filepath):
            raise ValueError('Filepaths in .coverage not recognized, try recreating the .coverage file manually.')


def get_mutations_by_file_from_cache(mutation_pk):
    from mutmut.cache import filename_and_mutation_id_from_pk
    filename, mutation_id = filename_and_mutation_id_from_pk(int(mutation_pk))
    return {filename: [mutation_id]}


def popen_streaming_output(
        cmd: str, callback: Callable[[Union[str, bytes]], None], timeout: Optional[float] = None
) -> int:
    """Open a subprocess and stream its output without hard-blocking.

    :param cmd: the command to execute within the subprocess
    :param callback: function that intakes the subprocess' stdout line by line.
        It is called for each line received from the subprocess' stdout stream.
    :param timeout: the timeout time of the subprocess
    :raises TimeoutError: if the subprocess' execution time exceeds
        the timeout time
    :return: the return code of the executed subprocess
    """
    if os.name == 'nt':  # pragma: no cover
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
        )
        stdout = process.stdout
    else:
        master, slave = os.openpty()
        process = subprocess.Popen(
            shlex.split(cmd, posix=True),
            stdout=slave,
            stderr=slave
        )
        stdout = os.fdopen(master)
        os.close(slave)

    def kill(process_):
        """Kill the specified process on Timer completion"""
        try:
            process_.kill()
        except OSError:
            pass

    # python 2-3 agnostic process timer
    timer = Timer(timeout, kill, [process])
    timer.daemon = True
    timer.start()

    while process.poll() is None:
        try:
            if os.name == 'nt':  # pragma: no cover
                line = stdout.readline()
                # windows gives readline() raw stdout as a b''
                # need to decode it
                line = line.decode("utf-8")
                if line:  # ignore empty strings and None
                    callback(line)
            else:
                while True:
                    line = stdout.readline()
                    if not line:
                        break
                    callback(line)
        except OSError:
            # This seems to happen on some platforms, including TravisCI.
            # It seems like it's ok to just let this pass here, you just
            # won't get as nice feedback.
            pass
        if not timer.is_alive():
            raise TimeoutError("subprocess running command '{}' timed out after {} seconds".format(cmd, timeout))
        process.poll()

    # we have returned from the subprocess cancel the timer if it is running
    timer.cancel()

    return process.returncode


def hammett_tests_pass(config: Config, callback) -> bool:
    # noinspection PyUnresolvedReferences
    from hammett import main_cli
    modules_before = set(sys.modules.keys())

    # set up timeout
    import _thread
    from threading import (
        Timer,
        current_thread,
        main_thread,
    )

    timed_out = False

    def timeout():
        _thread.interrupt_main()
        nonlocal timed_out
        timed_out = True

    assert current_thread() is main_thread()
    timer = Timer(config.baseline_time_elapsed * 10, timeout)
    timer.daemon = True
    timer.start()

    # Run tests
    try:
        class StdOutRedirect(TextIOBase):
            def write(self, s):
                callback(s)
                return len(s)

        redirect = StdOutRedirect()
        sys.stdout = redirect
        sys.stderr = redirect
        returncode = main_cli(shlex.split(config.test_command[len(hammett_prefix):]))
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        timer.cancel()
    except KeyboardInterrupt:
        timer.cancel()
        if timed_out:
            raise TimeoutError('In process tests timed out')
        raise

    modules_to_force_unload = {x.partition(os.sep)[0].replace('.py', '') for x in config.paths_to_mutate}

    for module_name in sorted(set(sys.modules.keys()) - set(modules_before), reverse=True):
        if any(module_name.startswith(x) for x in modules_to_force_unload) or module_name.startswith(
                'tests') or module_name.startswith('django'):
            del sys.modules[module_name]

    return returncode == 0


CYCLE_PROCESS_AFTER = 100


def run_mutation_tests(
        config: Config,
        progress: Progress,
        mutations_by_file: Dict[str, List[RelativeMutationID]],
        max_workers: int
):
    from mutmut.cache import update_mutant_status

    multiprocessing.set_start_method('spawn', force=True)
    mp_ctx = multiprocessing.get_context()

    mutants_queue = mp_ctx.Queue(maxsize=100)
    add_to_active_queues(mutants_queue)
    queue_mutants_thread = Thread(
        target=queue_mutants,
        name='queue_mutants',
        daemon=True,
        kwargs={
            'progress': progress,
            'config': config,
            'mutants_queue': mutants_queue,
            'mutations_by_file': mutations_by_file,
            'max_workers': max_workers,
        }
    )
    queue_mutants_thread.start()

    results_queue = mp_ctx.Queue(maxsize=100)
    add_to_active_queues(results_queue)

    def create_worker():
        t = Thread(
            target=check_mutants,
            name='check_mutants',
            daemon=True,
            kwargs={
                'mutants_queue': mutants_queue,
                'results_queue': results_queue,
                'cycle_process_after': CYCLE_PROCESS_AFTER,
                'max_workers': max_workers,
            }
        )
        t.start()
        return t

    t = create_worker()

    while True:
        command, status, filename, mutation_id = results_queue.get()
        if command == 'end':
            t.join()
            break

        elif command == 'cycle':
            t = create_worker()

        elif command == 'progress':
            if not config.swallow_output:
                print(status, end='', flush=True)
            elif not config.no_progress:
                progress.print()

        else:
            assert command == 'status'
            progress.register(status)

            update_mutant_status(file_to_mutate=filename, mutation_id=mutation_id, status=status,
                                 tests_hash=config.hash_of_tests)


def read_coverage_data() -> Dict[str, Dict[int, List[str]]]:
    """
    Reads the coverage database and returns a dictionary which maps the filenames to the covered lines and their contexts.
    """
    try:
        # noinspection PyPackageRequirements,PyUnresolvedReferences
        from coverage import Coverage
    except ImportError as e:
        raise ImportError(
            'The --use-coverage feature requires the coverage library. Run "pip install --force-reinstall mutmut[coverage]"') from e
    cov = Coverage('.coverage')
    cov.load()
    data = cov.get_data()
    return {filepath: data.contexts_by_lineno(filepath) for filepath in data.measured_files()}


def read_patch_data(patch_file_path: str):
    try:
        # noinspection PyPackageRequirements
        import whatthepatch
    except ImportError as e:
        raise ImportError(
            'The --use-patch feature requires the whatthepatch library. Run "pip install --force-reinstall mutmut[patch]"') from e
    with open(patch_file_path) as f:
        diffs = whatthepatch.parse_patch(f.read())

    return {
        os.path.normpath(diff.header.new_path): {change.new for change in diff.changes if change.old is None}
        for diff in diffs if diff.changes
    }


def add_mutations_by_file(
        mutations_by_file: Dict[str, List[RelativeMutationID]],
        filename: str,
        dict_synonyms: List[str],
        config: Optional[Config],
):
    with open(filename) as f:
        source = f.read()
    context = Context(
        source=source,
        filename=filename,
        config=config,
        dict_synonyms=dict_synonyms,
    )

    try:
        mutations_by_file[filename] = list_mutations(context)
        from mutmut.cache import register_mutants

        register_mutants(mutations_by_file)
    except Exception as e:
        raise RuntimeError(
            'Failed while creating mutations for {}, for line "{}"'.format(
                context.filename, context.current_source_line
            )
        ) from e


def python_source_files(
        path: str, tests_dirs: List[str], paths_to_exclude: Optional[List[str]] = None
) -> Iterator[str]:
    """Attempt to guess where the python source files to mutate are and yield
    their paths

    :param path: path to a python source file or package directory
    :param tests_dirs: list of directory paths containing test files
        (we do not want to mutate these!)
    :param paths_to_exclude: list of UNIX filename patterns to exclude

    :return: generator listing the paths to the python source files to mutate
    """
    paths_to_exclude = paths_to_exclude or []
    if isdir(path):
        for root, dirs, files in os.walk(path, topdown=True):
            for exclude_pattern in paths_to_exclude:
                dirs[:] = [d for d in dirs if not fnmatch.fnmatch(d, exclude_pattern)]
                files[:] = [f for f in files if not fnmatch.fnmatch(f, exclude_pattern)]

            dirs[:] = [d for d in dirs if os.path.join(root, d) not in tests_dirs]
            for filename in files:
                if filename.endswith('.py'):
                    yield os.path.join(root, filename)
    else:
        yield path


def compute_exit_code(
        progress: Progress, exception: Optional[Exception] = None, ci: bool = False
) -> int:
    """Compute an exit code for mutmut mutation testing

    The following exit codes are available for mutmut (as documented for the CLI run command):
     * 0 if all mutants were killed (OK_KILLED)
     * 1 if a fatal error occurred
     * 2 if one or more mutants survived (BAD_SURVIVED)
     * 4 if one or more mutants timed out (BAD_TIMEOUT)
     * 8 if one or more mutants caused tests to take twice as long (OK_SUSPICIOUS)

     Exit codes 1 to 8 will be bit-ORed so that it is possible to know what
     different mutant statuses occurred during mutation testing.

     When running with ci=True (--CI flag enabled), the exit code will always be
     1 for a fatal error or 0 for any other case.

    :param exception:
    :param progress:
    :param ci:

    :return: integer noting the exit code of the mutation tests.
    """
    code = 0
    if exception is not None:
        code = code | 1
    if ci:
        return code
    if progress.surviving_mutants > 0:
        code = code | 2
    if progress.surviving_mutants_timeout > 0:
        code = code | 4
    if progress.suspicious_mutants > 0:
        code = code | 8
    return code


hammett_prefix = 'python -m hammett '

# List of active multiprocessing queues
_active_queues = []


def add_to_active_queues(queue):
    _active_queues.append(queue)


def close_active_queues():
    for queue in _active_queues:
        queue.close()
