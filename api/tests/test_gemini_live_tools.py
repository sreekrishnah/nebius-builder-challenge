import pytest
from src.services.bidi import get_gemini_tools, TOOL_DESCRIPTIONS, is_tool_name

def test_tool_descriptions_count():
    assert len(TOOL_DESCRIPTIONS) >= 13
    assert is_tool_name('get_current_report')
    assert is_tool_name('get_report_source')
    assert is_tool_name('submit_voice_feedback')

def test_gemini_live_tool_declarations():
    tools = get_gemini_tools()
    assert len(tools) == 1
    tool = tools[0]
    func_names = [f.name for f in tool.function_declarations]
    
    assert "get_current_report" in func_names
    assert "get_report_source" in func_names
    assert "submit_voice_feedback" in func_names
    assert "get_user_context" in func_names
    assert "get_latest_developments" in func_names
    assert "control_briefing" in func_names

@pytest.mark.anyio
async def test_live_namespace_event_dispatch():
    import socketio
    from src.bidi_socket import register_live_socket
    sio = socketio.AsyncServer(async_mode='asgi')
    register_live_socket(sio)
    
    # Verify LiveNamespace was registered under /live
    assert '/live' in sio.namespace_handlers
    ns = sio.namespace_handlers['/live']
    
    # Test trigger_event routes live:end without error
    res = await ns.trigger_event('live:end', 'test-sid')
    assert res == {"ok": True}

def test_briefing_driver_progression():
    from src.services.briefingDriver import BriefingDriver, DriverChapter, ChapterScriptText
    
    chapters = [
        DriverChapter(
            title="Tech Rally",
            script=ChapterScriptText(facts="Facts here", sides="Sides here", forYou="For you here")
        )
    ]
    driver = BriefingDriver(chapters=chapters, listener="Alice", seconds_per_part=20, closing="Closing synthesis")
    
    # 1. Part 1
    note1 = driver.next_note()
    assert note1 is not None
    assert "part 1 of 3" in note1
    assert driver.running is True
    outcome1 = driver.turn_completed()
    assert outcome1 == 'next'
    assert driver.running is False
    
    # 2. Part 2
    note2 = driver.next_note()
    assert note2 is not None
    assert "part 2 of 3" in note2
    outcome2 = driver.turn_completed()
    assert outcome2 == 'next'
    
    # 3. Part 3
    note3 = driver.next_note()
    assert note3 is not None
    assert "part 3 of 3" in note3
    outcome3 = driver.turn_completed()
    assert outcome3 == 'next'
    
    # 4. Check-in question
    ask_note = driver.next_note()
    assert ask_note is not None
    assert "Ask Alice in one short" in ask_note
    outcome4 = driver.turn_completed()
    assert outcome4 == 'wait'
    assert driver.waiting_for == 'story'
    
    # Listener spoke and finished answering
    driver.listener_spoke()
    outcome_ans = driver.turn_completed()
    assert outcome_ans == 'next'
    assert driver.waiting_for is None
    
    # 5. Closing synthesis
    closing = driver.next_note()
    assert closing is not None
    assert "closing synthesis" in closing.lower()
    outcome_close = driver.turn_completed()
    assert outcome_close == 'next'
    
    # 6. Feedback question
    fb = driver.next_note()
    assert fb is not None
    assert "feedback" in fb.lower()
    outcome_fb = driver.turn_completed()
    assert outcome_fb == 'wait'
    assert driver.waiting_for == 'feedback'
    
    driver.wait_elapsed()
    
    # 7. Farewell
    bye = driver.next_note()
    assert bye is not None
    assert "goodbye" in bye.lower()
    outcome_bye = driver.turn_completed()
    assert outcome_bye == 'end'
    assert driver.finished is True

def test_briefing_driver_skip_and_interruption():
    from src.services.briefingDriver import BriefingDriver, DriverChapter
    
    chapters = [
        DriverChapter(title="Story 1"),
        DriverChapter(title="Story 2")
    ]
    driver = BriefingDriver(chapters=chapters, listener="Bob", seconds_per_part=15)
    
    # Part 1 interrupted
    note = driver.next_note()
    assert note is not None
    driver.listener_spoke()
    outcome = driver.turn_completed()
    assert outcome == 'wait'
    
    # Next note should have resume prefix
    resume_note = driver.next_note()
    assert "You were interrupted during this part" in resume_note
    
    # Second interruption moves on rather than looping forever
    driver.listener_spoke()
    outcome2 = driver.turn_completed()
    assert outcome2 == 'wait'
    note_part2 = driver.next_note()
    assert "part 2 of 3" in note_part2
    
    # Skip story jumps to Story 2
    driver.skip_story()
    assert driver.just_skipped() is True
    note_story2 = driver.next_note()
    assert "story 2 of 2" in note_story2


