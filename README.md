# Legal Document Generator

A Python tool that turns client case data into ready-to-review letters for an immigration law firm, as Word and PDF files, with one command.

> **Portfolio demo.** All clients and the firm are fictional. Every document is stamped
> **"DRAFT — FOR ATTORNEY REVIEW. NOT LEGAL ADVICE."**

## The problem

Law firm staff retype the same letters for every new client and check document checklists by hand. It's slow, and it's easy to send a letter with the wrong name or a missed document.

## What it does

For each client, it:

1. Reads their record (name, address, case type, documents received).
2. Compares the documents received against the checklist for their case type.
3. Writes an **engagement letter** confirming the firm will represent them.
4. Then writes **one** of these:
   - a **document request letter** listing exactly what's still missing, **or**
   - a **filing cover letter with an exhibit list**, if everything has arrived and the case is ready to file.
5. Checks every file and writes a summary report.

**Where AI is used:** Gemini writes only three short paragraphs (the scope of services, what happens next, and a friendly intro to the request letter). Names, addresses, dates, case IDs and document lists come straight from the data. The filing cover letter uses no AI, because a letter to the government should read the same every time.

## Run it

Requires Python 3.10+ and a free [Gemini API key](https://aistudio.google.com/apikey).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your key into .env
```

```bash
python main.py                          # all 10 sample clients
python main.py --client IMM-2026-0101   # one client
python main.py --offline                # no AI; uses standard paragraphs
pytest                                  # run the tests
```

The letters appear in `outputs/<case_id>/`, and the run summary is in `outputs/run_report.md`, `.docx` and `.pdf`.

## Sample output

| Engagement letter | Document request letter | Filing cover letter |
|---|---|---|
| ![Engagement letter](docs/images/sample_engagement_letter.png) | ![Document request letter](docs/images/sample_document_request.png) | ![Filing cover letter](docs/images/sample_filing_cover_letter.png) |

The run report shows, for every client, which letters were made, which documents are missing, and anything that needs attention.

## Built to be reliable

- **Safety checks on every file.** After writing, each file is re-opened to confirm no blank `{{ }}` fields were left and the DRAFT banner is on every page. A file that fails is deleted.
- **AI failures don't stop the run.** If Gemini is down, it retries once, then uses a standard paragraph and flags it in the report.
- **One bad client doesn't stop the rest.** Errors are logged and reported, and every other client is still processed.
- **Fewer API calls.** Paragraphs are written once per case type and reused for other clients of that type.

## Privacy and safety

- **All data is fake.** Phone numbers use the 555-01xx range and emails use `example.com`. The document checklists are simplified examples, not legal advice.
- **Client details never go to the AI.** Gemini only sees the case type and how many documents are missing.
- **A person always decides.** Every letter is a draft, and fees, filing fees and the filing address are left highlighted for the attorney to fill in.
- **The API key stays private.** It lives in `.env`, which is never committed.
- **In a real firm,** this would read from the firm's case management system with proper access controls instead of a JSON file.

## Project layout

```
main.py              runs everything
data/                sample clients and document checklists
src/                 data loading, AI drafting, letter templates, file building, checks, report
tests/               automated tests
docs/images/         sample letter previews
```

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
