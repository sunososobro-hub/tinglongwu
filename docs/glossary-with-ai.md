# Make your own glossary with your AI

Some words in your field come out wrong (product terms, team slang, acronyms). You can fix that yourself: ask the AI you already chat with (ChatGPT, Claude, Gemini, ...) to write a glossary file, then drop it into a folder. tinglongWu reads it on the next start. Nothing is uploaded and no model is retrained: it is a plain text word list that stays on your PC.

中文版：[glossary-with-ai.zh-TW.md](glossary-with-ai.zh-TW.md)

## How it works

1. **Copy the prompt below** into a new chat with your AI.
2. **Tell it your field**, the language you listen to and the language you want, and (optional) words that came out wrong. Example: "I listen to Russian network-engineering meetings, I want Traditional Chinese. 'частота' came out as 'cleanliness'."
3. **Save the answer** as a `.txt` file (UTF-8), e.g. `my-terms.txt`, in the glossary folder:
   - Windows: `%APPDATA%\tinglongwu\glossary\` (paste that into the Explorer address bar; create the folder if it is missing)
   - macOS: `~/Library/Application Support/tinglongwu/glossary/`
   - Linux: `~/.config/tinglongwu/glossary/`
4. **Restart tinglongWu.** The menu says how many glossary files it loaded. Your entries win over the built-in ones.

Privacy: the file never leaves your PC. Your AI chat is a cloud service, though: give it field names and example words, not confidential documents or meeting text.

## The prompt

```text
You write glossary files for tinglongWu, an offline real-time translator
(https://github.com/sunososobro-hub/tinglongwu). The format is documented at
the top of tinglongwu/glossary.py in that repo.

Ask me (one short message, in my language): my field, the language I LISTEN
to (source), the language I want (target), and any words that came out wrong.
Then output ONLY the file content, in one code block, nothing else.

File format (UTF-8, one entry per line):
  source = target                 a term and its translation
  source | variant | variant = target
                                  variants: other spellings, inflected forms
                                  (Russian cases), common mis-hearings
  term                            no "=": a hint word kept as written
                                  (names, acronyms)
  source = target ~ wrong1 | wrong2
                                  wrong renderings to replace in the output
  # comment

Rules:
- 100 to 300 entries, the words people in my field actually say in meetings.
- Source side in the source language as it is spoken (include loanwords and
  slang); target side in the target language (Traditional Chinese = Taiwan
  terms, Simplified = Mainland terms).
- Skip everyday words that also have a normal meaning (they would misfire in
  ordinary talk). When in doubt, leave it out.
- Put the words I said came out wrong first, with their wrong renderings
  after "~".
- Do not invent terms; no long sentences; one term per line.
- Start the file with "# <field>: <source> -> <target>, made with AI".

After the code block, tell me in one line where to save it:
Windows %APPDATA%\tinglongwu\glossary\  ·  macOS ~/Library/Application Support/tinglongwu/glossary/
·  Linux ~/.config/tinglongwu/glossary/
```

## Share it

A good list helps everyone in your field. Built-in domain files live in `src/core/tinglongwu/data/terms/`: open a PR adding a file there (format in CONTRIBUTING.md), or post your list in [Discussions](https://github.com/sunososobro-hub/tinglongwu/discussions).
