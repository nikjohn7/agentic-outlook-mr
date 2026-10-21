# Propose read-together companion groups among same-firm sources

Some asset managers publish a set of documents that an analyst reads together as
ONE combined source — a market review paired with its outlook, an explicitly
multi-part series, or a monthly note that updates the same franchise as a
quarterly. When two documents are genuine companions like this, reading them
together lets the downstream pipeline resolve duplicate and conflicting calls
with full context.

You are given ONLY source metadata — firm, title, and date. You never see the
documents. Your job is to propose, conservatively, which same-firm sources are
clear companions that should be read as one combined source, and to state
plainly which firms you are leaving ungrouped and why.

## The bar for grouping is high

Most same-firm sources are independent desk pieces — an equity outlook and a
fixed-income outlook from the same house are DIFFERENT views on DIFFERENT asset
classes and must stay separate. **Same firm alone is never a reason to group.**
The default answer for a firm is "no grouping".

Propose a group ONLY when the titles and dates show a clear companion signal:

- **Same series + same period** — e.g. a "Q2 review" and a "Q2 outlook", or a
  "mid-year review" and a "mid-year outlook", from the same firm over the same
  window.
- **Explicit multi-part title** — e.g. "Part 1 / Part 2", or a title that names
  the other document as its companion.
- **Monthly + quarterly of the same franchise** covering the same window — e.g.
  a "Monthly Asset Allocation Update" and a "Quarterly Asset Allocation
  Outlook" from the same firm, close in date.

Do NOT group on any of these alone:

- Same firm, same publication month, but different asset classes or desks
  (equity vs fixed income vs macro vs real assets) — these are independent.
- A shared umbrella label (e.g. every piece tagged "2026 Midyear Outlook") when
  the individual titles cover different asset classes.
- A guess that two documents "probably relate". When in doubt, do NOT group.

A source belongs to at most one group. Use ONLY the `source_id` values given in
the machine-readable inputs below.

## What to return for every firm

For each firm in the inputs:

- If you propose one or more groups, list each group with its member
  `source_id`s and a one-line reason naming the companion signal.
- If you leave the firm ungrouped (in whole or in part), add one
  `ungrouped_firms` entry for it with a one-line reason (e.g. "three separate
  desk outlooks — equity, fixed income, macro — different asset classes").

Every firm in the inputs must be accounted for: it appears in `groups`, in
`ungrouped_firms`, or both (when only some of its sources form a group).

## Output contract

Return exactly one JSON object and nothing else:

```json
{
  "groups": [
    {
      "firm": "<firm name>",
      "source_ids": ["<source_id>", "<source_id>"],
      "reason": "<one line: the companion signal>"
    }
  ],
  "ungrouped_firms": [
    {
      "firm": "<firm name>",
      "reason": "<one line: why these sources stay independent>"
    }
  ]
}
```

If no firm has a clear companion signal, return `{"groups": [], "ungrouped_firms": [...]}`
with one entry per firm explaining why it stays independent.
