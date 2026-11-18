import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from wizzy.mistake_bank import project, render_pdf, story, worksheet_lines


def test_spelling_only_success_never_establishes_dictation_mastery_or_merges_different_words():
    def word(number, answer, verdict, basis):
        return dict(number=number, item_type='word', grading_basis=basis, verdict=verdict,
                    student_answer='applle', reasoning='同样的通用解释，不应该合并不同的单词', steps=[], answer=answer)
    items = [word('第1项', 'apple', 'correct', 'spelling_only'),
             word('第2项', '', 'unclear', 'none'),
             word('第3项', 'apple', 'incorrect', 'spelling_only'),
             word('第4项', 'banana', 'incorrect', 'spelling_only'),
             word('第5项', 'apple', 'correct', 'task_reference'),
             word('第6项', '', 'incorrect', 'task_reference')]
    projected = project(json.dumps(dict(pages=[dict(position=1, subject='english', note='', questions=items)])), 1)
    assert [q['number'] for q in projected] == ['第3项', '第4项', '第5项']
    assert projected[0]['stem_key'] != projected[1]['stem_key']
    assert projected[0]['stem_key'] == projected[2]['stem_key']


def test_repeated_answer_story_and_question_sheet_leave_the_answer_out():
    zone = ZoneInfo('Asia/Shanghai')
    attempts = [
        {'student_answer': '公鸡13只', 'verdict': 'incorrect', 'submitted_at': datetime(2026, 11, 10, tzinfo=zone)},
        {'student_answer': '公鸡 13 只', 'verdict': 'incorrect', 'submitted_at': datetime(2026, 11, 12, tzinfo=zone)},
        {'student_answer': '公鸡10只', 'verdict': 'correct', 'submitted_at': datetime(2026, 11, 14, tzinfo=zone)},
    ]
    timeline, summary, peak, note = story(attempts)
    assert timeline[0].startswith('2026年11月10日 第1次，错，写成公鸡13只')
    assert timeline[-1].startswith('2026年11月14日 第3次，对，写成公鸡10只')
    assert summary == '前2次答案相同，第3次变了。'
    assert peak == 2 and '2 次' in note
    question = [{'stem': '第6题 鸡兔同笼', 'reference_answer': '公鸡10只'}]
    questions = '\n'.join(worksheet_lines(question, 'questions'))
    answers = '\n'.join(worksheet_lines(question, 'answers'))
    assert '参考答案' not in questions
    assert '参考答案：公鸡10只' in answers
    first, second = render_pdf(questions.splitlines()), render_pdf(answers.splitlines())
    assert first.startswith(b'%PDF') and second.startswith(b'%PDF') and first != second
    PdfReader(io.BytesIO(first))
    PdfReader(io.BytesIO(second))
