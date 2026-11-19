#!/usr/bin/env python3
"""End-to-end smoke test against a RUNNING VeriHire server (local or deployed).

    python scripts/smoke_test.py --base-url http://localhost:8000
    python scripts/smoke_test.py --base-url https://<space>.hf.space --token "<supabase access token>" --require-real

Flow: health -> me -> upload synthetic resumes -> poll job -> JD -> search -> analysis ->
asserts (citations verbatim, blind responses carry no PII, ranking sensible, PDF link works) -> cleanup.
Exit code 0 = all good. Uses only fictional data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from devtools import synthpdf  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class SmokeFailure(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SmokeFailure(msg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--token", default="dev:smoke@example.com", help="Bearer token (dev:<email> or Supabase JWT)")
    p.add_argument("-n", "--resumes", type=int, default=5)
    p.add_argument("--timeout", type=float, default=180, help="seconds to wait for ingestion")
    p.add_argument("--require-real", action="store_true", help="fail if any ML component is a stub")
    p.add_argument("--keep", action="store_true", help="do not delete the uploaded documents")
    a = p.parse_args(argv)

    base = a.base_url.rstrip("/")
    api = f"{base}/api/v1"
    h = {"Authorization": f"Bearer {a.token}"}
    seed = int(time.time()) % 1_000_000
    uploaded: list[str] = []
    steps = 0

    def ok(msg: str) -> None:
        nonlocal steps
        steps += 1
        print(f"  ✓ {msg}")

    print(f"VeriHire smoke test -> {base}")
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        try:
            hz = c.get(f"{api}/health").json()
            check(hz.get("status") == "ok", f"health not ok: {hz}")
            if a.require_real:
                check(not hz.get("stubs_in_use"), f"stub ML components in use: {hz.get('ml_components')}")
            ok(f"health ok (ml_mode={hz['ml_mode']}, embedder={hz['embedder']}, stubs_in_use={hz['stubs_in_use']})")

            me = c.get(f"{api}/me", headers=h)
            check(me.status_code == 200, f"/me returned {me.status_code}: {me.text}")
            ok(f"authenticated as {me.json().get('email')}")
            check(c.get(f"{api}/me").status_code == 401, "/me without token must be 401")
            ok("unauthenticated request rejected")

            best = synthpdf.make_resume(seed, role="backend", skills=["Python", "FastAPI", "Docker", "PostgreSQL"], years=6)
            files = [("files", (f"smoke-{seed}-{i}.pdf", synthpdf.make_resume(seed + 1 + i).pdf, "application/pdf"))
                     for i in range(a.resumes)]
            files.append(("files", (f"smoke-{seed}-best.pdf", best.pdf, "application/pdf")))
            files.append(("files", ("not-a-pdf.txt", b"hello", "text/plain")))
            up = c.post(f"{api}/documents/upload", headers=h, files=files)
            check(up.status_code == 202, f"upload returned {up.status_code}: {up.text}")
            body = up.json()
            uploaded = body["document_ids"]
            check(len(uploaded) == a.resumes + 1, f"expected {a.resumes + 1} accepted, got {body}")
            check(any(r["reason"] == "not_pdf" for r in body["rejected"]), "non-PDF was not rejected")
            ok(f"uploaded {len(uploaded)} PDFs, non-PDF rejected")

            deadline = time.time() + a.timeout
            job = {}
            while time.time() < deadline:
                job = c.get(f"{api}/jobs/{body['job_id']}", headers=h).json()
                if job.get("status") in ("completed", "completed_with_errors"):
                    break
                time.sleep(1)
            check(job.get("status") == "completed", f"ingestion did not finish cleanly in {a.timeout}s: {job}")
            check(job["done"] == len(uploaded), f"some documents failed: {job}")
            ok(f"ingestion completed ({job['done']}/{job['total']} ready)")

            jd = c.post(f"{api}/job-descriptions", headers=h, json={"text": synthpdf.JD_BACKEND})
            check(jd.status_code == 201, f"JD create returned {jd.status_code}: {jd.text}")
            jd = jd.json()
            check("python" in jd["spec"]["required_skills"], f"JD parse missed python: {jd['spec']}")
            ok(f"JD parsed: required={jd['spec']['required_skills']}")

            t = time.perf_counter()
            sr = c.post(f"{api}/search", headers=h, json={"jd_id": jd["id"], "top_k": 10})
            ms = (time.perf_counter() - t) * 1000
            check(sr.status_code == 200, f"search returned {sr.status_code}: {sr.text}")
            res = sr.json()
            check(res["results"], f"search returned no results: {res['warnings']}")
            mine = [r for r in res["results"] if r["document_id"] in uploaded]
            check(mine and mine[0]["document_id"] == uploaded[-1],
                  "the ideal candidate is not the top match among the smoke-test resumes")
            text = json.dumps(res)
            check(best.email not in text and best.name not in text and not EMAIL.search(text), "PII leaked in blind search")
            ok(f"search ok in {ms:.0f} ms; ideal candidate ranked first, no PII in response")

            cid = mine[0]["candidate_id"]
            t = time.perf_counter()
            an = c.post(f"{api}/analysis", headers=h, json={"jd_id": jd["id"], "candidate_id": cid}, timeout=120)
            check(an.status_code == 200, f"analysis returned {an.status_code}: {an.text}")
            an = an.json()
            for cit in an["citations_resolved"]:
                check(cit["text_scrubbed"][cit["start"]:cit["end"]] == cit["quote"], f"citation not verbatim: {cit}")
            ok(f"analysis ok in {(time.perf_counter() - t):.1f}s; {len(an['citations_resolved'])} citations, all verbatim")

            cand = c.get(f"{api}/candidates/{cid}", headers=h).json()
            check(cand.get("pii") is None and best.email not in json.dumps(cand), "candidate profile leaked PII")
            ok("candidate profile is blind")

            link = c.get(f"{api}/documents/{uploaded[-1]}/pdf-url", headers=h).json()["url"]
            pdf = c.get(link if link.startswith("http") else base + link)
            check(pdf.status_code == 200 and pdf.content[:5] == b"%PDF-", f"PDF link failed ({pdf.status_code})")
            ok("signed PDF link works")
        except (SmokeFailure, httpx.HTTPError, KeyError, ValueError) as e:
            print(f"  ✗ FAILED: {e}")
            return 1
        finally:
            if uploaded and not a.keep:
                for d in uploaded:
                    c.delete(f"{api}/documents/{d}", headers=h)
                print(f"  (cleaned up {len(uploaded)} documents)")
    print(f"SMOKE TEST PASSED ({steps} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
