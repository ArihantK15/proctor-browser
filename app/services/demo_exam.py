"""Seed a demo exam with sample questions on new teacher signup."""

import json
import uuid as _uuid
import logging

log = logging.getLogger(__name__)

DEMO_QUESTIONS = [
    {
        "question": "What does ACID stand for in databases?",
        "question_type": "mcq_single",
        "options": {
            "A": "Atomicity, Consistency, Isolation, Durability",
            "B": "Automated, Consistent, Isolated, Durable",
            "C": "Atomic, Concurrent, Isolated, Durable",
            "D": "Automated, Concurrent, Isolated, Durable",
        },
        "correct": "A",
        "reference_answer": "",
        "rubric": "",
        "max_score": 1,
    },
    {
        "question": "Which of these sorting algorithms has an average time complexity of O(n log n)?",
        "question_type": "mcq_single",
        "options": {
            "A": "Bubble Sort",
            "B": "Insertion Sort",
            "C": "Merge Sort",
            "D": "Selection Sort",
        },
        "correct": "C",
        "reference_answer": "",
        "rubric": "",
        "max_score": 1,
    },
    {
        "question": "Explain the concept of a RESTful API. What are the key principles?",
        "question_type": "short_answer",
        "options": {},
        "correct": "",
        "reference_answer": (
            "REST (Representational State Transfer) is an architectural style for designing "
            "networked applications. Key principles include: stateless client-server communication, "
            "uniform interface (using HTTP methods GET/POST/PUT/DELETE on resources identified by URLs), "
            "cacheable responses, and layered system architecture."
        ),
        "rubric": "Award full marks if mention client-server, stateless, HTTP methods on resources, uniform interface.",
        "max_score": 5,
    },
]


async def seed_demo_exam(teacher_id: str, *, _atable=None) -> str:
    """Create a demo exam with sample questions for a new teacher.
    Returns the exam_id of the created demo exam.
    """
    if _atable is None:
        from ..database import async_table as _atable

    exam_id = str(_uuid.uuid4())
    title = "Demo Exam \u2014 Quick Start"
    query = _atable("exam_config").insert({
        "exam_id":          exam_id,
        "teacher_id":       teacher_id,
        "exam_title":       title,
        "duration_minutes": 30,
        "access_code":      "DEMO",
    })

    try:
        await query.execute()
    except Exception as e:
        log.warning("[demo_exam] failed to create exam_config: %s", e)
        return ""

    rows = []
    for i, q in enumerate(DEMO_QUESTIONS):
        rows.append({
            "question_id":      str(i + 1),   # TEXT column (phase146)
            "exam_id":          exam_id,
            "teacher_id":       teacher_id,
            "question":         q["question"],
            "question_type":    q["question_type"],
            "options":          json.dumps(q["options"]) if q["options"] else "{}",
            "correct":          q["correct"],
            "reference_answer": q["reference_answer"],
            "rubric":           q["rubric"],
            "max_score":        q["max_score"],
        })

    try:
        await _atable("questions").insert(rows).execute()
    except Exception as e:
        log.warning("[demo_exam] failed to insert demo questions: %s", e)

    log.info("[demo_exam] seeded demo exam %s for teacher %s", exam_id[:8], teacher_id)
    return exam_id
