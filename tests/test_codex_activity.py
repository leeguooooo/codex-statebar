import json

from codex_statebar.activity import read_activity


def write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    return str(path)


def call(name, ident='c1', **args):
    return {'type': 'response_item', 'payload': {'type': 'function_call',
            'call_id': ident, 'name': name, 'arguments': json.dumps(args)}}


def test_archived_codex_format_and_namespaced_tool(tmp_path):
    path = write(tmp_path / 'archived_sessions/export.jsonl', [
        call('functions.exec_command', cmd='python -m pytest')])
    assert read_activity(path).active_tool == ('Bash', '')


def test_directory_name_does_not_misclassify_claude(tmp_path):
    path = write(tmp_path / 'sessions/claude.jsonl', [
        {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 't1', 'name': 'Read',
             'input': {'file_path': '/tmp/example.py'}}]}}])
    assert read_activity(path).active_tool == ('Read', 'example.py')


def test_completed_codex_tool_is_not_running(tmp_path):
    path = write(tmp_path / 'export.jsonl', [call('functions.exec_command'),
        {'type': 'response_item', 'payload': {'type': 'function_call_output',
                                            'call_id': 'c1', 'output': 'done'}}])
    info = read_activity(path)
    assert info.active_tool is None
    assert info.completed_counts == [('Bash', 1)]


def test_aborted_tool_is_not_running_forever(tmp_path):
    path = write(tmp_path / 'export.jsonl', [call('functions.exec_command'),
        {'type': 'event_msg', 'payload': {'type': 'task_aborted'}}])
    assert read_activity(path).active_tool is None


def test_new_turn_does_not_revive_previous_unfinished_tool(tmp_path):
    path = write(tmp_path / 'export.jsonl', [call('functions.exec_command'),
        {'type': 'event_msg', 'payload': {'type': 'task_started'}},
        {'type': 'turn_context', 'payload': {'model': 'gpt-test'}}])
    assert read_activity(path).active_tool is None


def test_latest_namespaced_plan(tmp_path):
    path = write(tmp_path / 'export.jsonl', [
        call('functions.update_plan', plan=[{'step': 'Check', 'status': 'pending'}]),
        call('functions.update_plan', 'c2', plan=[{'step': 'Check', 'status': 'completed'}])])
    info = read_activity(path)
    assert info.todos_done == info.todos_total == 1
