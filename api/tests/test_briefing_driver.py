from src.services.briefingDriver import BriefingDriver, DriverChapter, RESUME_PREFIX

chapters = [DriverChapter(title='Storage policy'), DriverChapter(title='Green hydrogen')]


def make() -> BriefingDriver:
    return BriefingDriver(chapters, 'Sam', 50)


def test_interrupted_part_is_repeated_once_after_a_pause():
    d = make()
    d.next_note()
    d.listener_spoke()
    assert d.turn_completed() == 'wait'
    assert RESUME_PREFIX in d.next_note()
    d.listener_spoke()
    d.turn_completed()
    assert 'part 2 of 3' in d.next_note()


def test_move_on_after_interruption_goes_to_next_story_without_repeating():
    d = make()
    d.next_note()
    d.listener_spoke()
    d.skip_story()
    assert d.just_skipped()
    assert d.turn_completed() == 'next'
    note = d.next_note()
    assert RESUME_PREFIX not in note and 'story 2' in note


def test_answer_between_parts_pauses_then_carries_on():
    d = make()
    d.listener_spoke()
    assert d.answer_pending
    assert d.turn_completed() == 'wait'
    assert not d.answer_pending
    assert d.turn_completed() == 'next'


def test_speaking_over_the_farewell_ends_the_call():
    d = BriefingDriver([DriverChapter(title='Only story')], 'Sam', 50)
    for _ in range(6):
        d.next_note()
        d.turn_completed()
        if d.waiting_for:
            d.wait_elapsed()
    assert 'goodbye' in d.next_note()
    d.listener_spoke()
    assert d.turn_completed() == 'end'
    assert d.in_closing
