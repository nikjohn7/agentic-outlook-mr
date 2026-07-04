# Resolve analyst group notes into source groups

Financial analysts sometimes review two documents from the same firm as ONE
combined source — typically a market review paired with an outlook. They flag
this in free-text "group notes": informal lines naming which titles belong
together.

Your only job is translation: map each note onto the sources actually present
in this run. You never decide groupings yourself, and you never merge sources
the notes do not pair.

Rules:

- Use ONLY `source_id` values from the machine-readable `sources` list below.
- A group needs at least two sources. Never invent a group the notes do not
  state, and never add a source a note does not reference.
- Note wording is informal — match tolerantly (partial titles, firm names,
  quarter references). But if you cannot confidently identify EVERY source a
  note names among this run's sources, put the whole note line in
  `unmatched_notes` instead of guessing: a wrong guess silently merges the
  wrong documents, while an unmatched note is flagged to the analyst.
- A source belongs to at most one group.
- Ignore note lines that are not grouping instructions.

## Analyst group notes

{{group_notes}}

## Output contract

Return exactly one JSON object and nothing else:

```json
{
  "groups": [
    {
      "source_ids": ["<source_id>", "<source_id>"],
      "note": "the note line this group came from"
    }
  ],
  "unmatched_notes": ["note lines you could not confidently map"]
}
```

If the notes contain no resolvable groupings, return
`{"groups": [], "unmatched_notes": [...]}`.
