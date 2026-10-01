"""Answer follow-up questions about an engagement's certificates."""

from __future__ import annotations

import csv
import io

import anthropic

from .rules import EngagementResult, Policy

MODEL = "claude-opus-5-5"

SYSTEM = """\
You are the analyst who reviewed this client's exemption and resale \
certificates. Answer the reviewer's follow-up questions from the engagement \
data provided: the schedule, the lines that were excluded and why, the flags, \
and what was read on each certificate. Cite source file names so the reviewer \
can open the document. If the data does not answer the question, say so and \
say what would (for example, which certificate to request from the client). \
Keep answers short and concrete; use a small table when listing many items.\
"""


def engagement_context(engagement: dict, result: EngagementResult, policy: Policy) -> str:
    out = io.StringIO()
    out.write(f"Client: {engagement['client_name']}\nSeller: {engagement.get('seller_name') or 'n/a'}\n")
    out.write(f"Review policy: {policy.as_dict()}\n\n")

    out.write("## Schedule (company, type, state, registration, source file, flags)\n")
    w = csv.writer(out)
    for r in result.rows:
        w.writerow([r.company, r.certificate_type, r.state, r.registration, r.source_file,
                    "|".join(dict.fromkeys(r.flags))])

    out.write("\n## Lines on forms that were not scheduled\n")
    for x in result.excluded:
        w.writerow([x.company, x.state, x.raw_text, x.classification, x.reason, x.source_file])

    out.write("\n## Certificates\n")
    for c in result.certificates:
        ex = c.extraction
        out.write(f"- {c.cert['filename']} [status={c.cert['status']}, batch={c.cert.get('batch_label')}]")
        if ex:
            out.write(f" form={ex.form_name}; type={ex.certificate_type}; purchaser={ex.purchaser.name}; "
                      f"reason={ex.exemption_reason}; expires={ex.expiration_date}; signed={ex.signed}")
        if c.cert.get("reviewer_note"):
            out.write(f"; reviewer note: {c.cert['reviewer_note']}")
        out.write("\n")
        for f in c.flags:
            out.write(f"    flag {f.code} ({f.severity}): {f.message}\n")
    return out.getvalue()


def ask(question: str, context: str, history: list[tuple[str, str]],
        client: anthropic.Anthropic | None = None) -> str:
    client = client or anthropic.Anthropic()
    messages: list[dict] = []
    for q, a in history[-10:]:
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": question})

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=[
            {"type": "text", "text": SYSTEM},
            {"type": "text", "text": "<engagement_data>\n" + context + "\n</engagement_data>",
             "cache_control": {"type": "ephemeral"}},
        ],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        return "The model declined to answer this question."
    return "".join(b.text for b in message.content if b.type == "text").strip()
