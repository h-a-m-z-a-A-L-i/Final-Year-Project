import threading
import time
from datetime import datetime, timezone


def handle_chat_request(ctx: dict, msg: dict):
    dep_manager = ctx['dep_manager']
    memory_store = ctx['memory_store']
    send_msg = ctx['send_msg']
    run_stream = ctx['run_stream']
    stop_stream = ctx['stop_stream']
    ACTIVE_STREAMS = ctx['ACTIVE_STREAMS']
    ACTIVE_STREAMS_LOCK = ctx['ACTIVE_STREAMS_LOCK']
    extract_cell_number = ctx['extract_cell_number']
    build_profile_memory_context = ctx['build_profile_memory_context']
    extract_user_profile_facts = ctx['extract_user_profile_facts']
    history_key = ctx['history_key']
    max_context_chars = ctx['max_context_chars']
    MAX_HISTORY_MESSAGES = ctx['MAX_HISTORY_MESSAGES']

    url = history_key(msg.get('url'))
    prompt = str(msg.get('prompt', ''))
    tab_id = msg.get('tabId')
    session_id = str(msg.get('sessionId') or 'default')
    if not url:
        send_msg({'type': 'CHAT_RESPONSE', 'error': 'Missing or invalid notebook URL.', 'tabId': tab_id})
        return

    context = ''
    builder = dep_manager.get_builder(url)
    mode = 'simple'
    if builder:
        cell_num = extract_cell_number(prompt)
        if cell_num is not None:
            context = builder.get_cell_context(cell_num)
            mode = 'ask'

    extracted_facts = extract_user_profile_facts(prompt)
    for k, v in extracted_facts.items():
        memory_store.upsert_fact(url, k, v, session_id=session_id)

    facts = memory_store.get_facts(url, session_id=session_id)
    profile_context = build_profile_memory_context(facts)
    if profile_context:
        context = f"{profile_context}\n\n{context}" if context else profile_context
    if context and len(context) > max_context_chars:
        context = context[:max_context_chars]

    history = memory_store.get_history(url, session_id=session_id)
    memory_store.append(url, 'user', prompt, session_id=session_id)

    active_key = str(tab_id)
    with ACTIVE_STREAMS_LOCK:
        prev = ACTIVE_STREAMS.get(active_key)
        stop_stream(prev)
    if prev and prev.get('sessionId'):
        # signal remote stop is orchestrator's responsibility
        pass

    worker = threading.Thread(target=run_stream, args=(url, prompt, tab_id, session_id, history, context, mode), daemon=True)
    with ACTIVE_STREAMS_LOCK:
        ACTIVE_STREAMS[active_key] = {'thread': worker, 'sessionId': session_id, 'stopped': False, 'url': url}
    worker.start()


def handle_stop_chat(ctx: dict, msg: dict):
    ACTIVE_STREAMS = ctx['ACTIVE_STREAMS']
    ACTIVE_STREAMS_LOCK = ctx['ACTIVE_STREAMS_LOCK']
    stop_stream = ctx['stop_stream']
    send_msg = ctx['send_msg']
    history_key = ctx['history_key']

    tab_id = msg.get('tabId')
    active_key = str(tab_id)
    session_id = str(msg.get('sessionId') or '')
    with ACTIVE_STREAMS_LOCK:
        state = ACTIVE_STREAMS.get(active_key)
        if state:
            stop_stream(state)
            if not session_id:
                session_id = str(state.get('sessionId') or '')
    # Orchestrator may handle remote stop signaling.
    send_msg({'type': 'CHAT_STREAM_END', 'stopped': True, 'tabId': tab_id, 'url': history_key(msg.get('url')), 'sessionId': session_id})


def handle_prompt_signal(ctx: dict, msg: dict):
    send_msg = ctx['send_msg']
    log = ctx['log']
    tab_url = ctx['history_key'](msg.get('tabUrl') or '')
    cell_index = msg.get('cellIndex')
    exec_order = msg.get('execOrder')
    text = str(msg.get('text') or '').strip()
    exec_ts = msg.get('ts')
    print(f"[RECV-SIGNAL] cell={cell_index}, order={exec_order}, ts={exec_ts}, text='{text}'")
    log(f"PROMPT_SIGNAL cell={cell_index if cell_index is not None else '?'} order={exec_order} text={text} ts={exec_ts}")
    # The orchestrator will act on the signal (e.g., update execution state)
