"""Storage abstraction: local (HMAC links, traversal-safe) and Supabase (mocked HTTP)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.errors import AppError
from app.services.storage import LocalStorage, StorageError, SupabaseStorage, make_key, validate_key


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("key", ["../x.pdf", "a/../../b.pdf", "/abs.pdf", "a/b.txt", "a b.pdf", "a/./b.pdf", "", "a//b.pdf"])
def test_validate_key_rejects(key):
    with pytest.raises(StorageError):
        validate_key(key)


def test_make_key_hides_owner_and_is_valid():
    k = make_key("user@example.com", "0b1c2d3e-0000-4000-8000-000000000000")
    assert "user" not in k and k.endswith("/0b1c2d3e-0000-4000-8000-000000000000.pdf")


def test_local_roundtrip_and_signed_url(tmp_path):
    now = [1_000_000.0]
    st = LocalStorage(tmp_path, "secret", clock=lambda: now[0])
    key = "owner/doc1.pdf"
    run(st.put(key, b"%PDF-1.4 data"))
    assert run(st.get(key, 100)) == b"%PDF-1.4 data"
    with pytest.raises(StorageError):
        run(st.get(key, 5))  # size cap
    url = run(st.signed_url(key, 60))
    q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
    assert st.verify("owner/doc1.pdf", int(q["exp"]), q["sig"]).read_bytes() == b"%PDF-1.4 data"
    with pytest.raises(AppError):
        st.verify("owner/doc1.pdf", int(q["exp"]), q["sig"][:-2] + "AA")  # tampered
    with pytest.raises(AppError):
        st.verify("owner/other.pdf", int(q["exp"]), q["sig"])  # signature bound to key
    now[0] += 61
    with pytest.raises(AppError):
        st.verify("owner/doc1.pdf", int(q["exp"]), q["sig"])  # expired
    run(st.delete(key))
    run(st.delete(key))  # idempotent
    with pytest.raises(StorageError):
        run(st.get(key, 100))


def test_supabase_storage_calls():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, str(req.url)))
        assert req.headers["authorization"] == "Bearer svc" and req.headers["apikey"] == "svc"
        if req.method == "POST" and "/object/sign/" in str(req.url):
            return httpx.Response(200, json={"signedURL": "/object/sign/resumes/o/d.pdf?token=abc"})
        if req.method == "POST":
            assert req.headers["content-type"] == "application/pdf"
            return httpx.Response(200, json={"Key": "resumes/o/d.pdf"})
        if req.method == "GET":
            return httpx.Response(200, content=b"%PDF-" + b"x" * 50)
        if req.method == "DELETE":
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    st = SupabaseStorage("https://p.supabase.co/", "svc", "resumes", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    run(st.put("o/d.pdf", b"%PDF-1"))
    assert run(st.get("o/d.pdf", 1000)).startswith(b"%PDF-")
    with pytest.raises(StorageError):
        run(st.get("o/d.pdf", 10))
    assert run(st.signed_url("o/d.pdf", 300)) == "https://p.supabase.co/storage/v1/object/sign/resumes/o/d.pdf?token=abc"
    run(st.delete("o/d.pdf"))
    assert ("POST", "https://p.supabase.co/storage/v1/object/resumes/o/d.pdf") in seen
    assert ("DELETE", "https://p.supabase.co/storage/v1/object/resumes") in seen


def test_supabase_storage_errors():
    st = SupabaseStorage("https://p.supabase.co", "svc", "resumes",
                         http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    for coro in (st.put("o/d.pdf", b"x"), st.get("o/d.pdf", 10), st.signed_url("o/d.pdf", 5)):
        with pytest.raises(StorageError):
            run(coro)
