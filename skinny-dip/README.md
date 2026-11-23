# skinny-dip (dip-args)

A canvas for walking the corpus by argument. It is a copy of `../../dip-memory/skinny-dip`,
changed to work against the dip-args API (`../dipa/api.py`). The original still serves
dip-memory, so changes here don't affect it.

```bash
# the API first: DIP_DATA_DIR=data-full uvicorn dipa.api:app --port 8001  (from ../)
DIP_API_TARGET=http://localhost:8001 npx vite --port 5174
```

Vite proxies `/api/*` to the API (`vite.config.ts`). `node_modules` is a symlink to
dip-memory's copy. Run `npx tsc -b` to type-check.

## What's different from dip-memory's skinny-dip

- **Seed tabs:** Themes, Issues, Arguments, Holders, Images (`SeedsPanel.tsx`). The filter also
  matches holder names. The title bar links to the wiki editor ("wiki ↗").
- **Node kinds:** argument, issue, theme, holder, passage, plus image nodes (`api.ts`). Ids are
  strings (`a…`, `i…`, `t…`, `p<n>`, `h:<slug>`; images by slug).
- **Moves per kind** (`MOVES` in `api.ts`; styles in `relations.ts`):
  - argument: Counter, Evidence, Neighbours, Zoom in/out, Shared with, Sources, Holder, Images
  - issue: Zoom in/out, Neighbours, Images
  - theme: Zoom in, Neighbours, Images
  - holder: Holder
  - passage: Arguments here, Images
  - image: Arguments (the reverse search)
- **Node rendering** (`PassageNode.tsx`):
  - holder chip, coloured by holder type
  - provenance label: "Summary · written by qwen3:14b", "Summary · edited", or
    "Passage · verbatim from …"
  - reception lines ("rejected by the author of “…”"), linked to each document's URL
  - the quotes, in a collapsible block when selected; an unverified quote is marked ⚠
  - passages in a serif face with a dashed border, the quote that led to them highlighted
- **Results panel:** shows the holder, a verbatim style for passages, and image thumbnails with
  their credit. Choosing an image result adds an image node.
- Credits on images are always shown (tooltip on the node, line in the results).

## Ask (questions to the LLM)

`AskPanel.tsx`, state in `store/graphs.ts` (`ask`, `openAsk`, `submitQuestion`, `addCited`,
`pinAnswer`), API call `askQuestion` in `api.ts` (`POST /question`, see `../README.md`).

- **Ask about a card:** every card's toolbar has an Ask button.
- **Ask about a part of it:** a selected card's text can be selected (the text is `nodrag` while
  the card is selected); an "Ask about “…”" button appears above the card and the panel quotes
  the selection. The selection is read from `selectionchange`, only inside that card's text.
- **Ask the whole corpus:** the "ask" button under "clear".
- **The panel** (same slot as the results panel; opening one closes the other): question box
  (Enter sends, Shift+Enter is a new line), the answer with citation chips (`[A2]` …), then every
  source used, cited first. Clicking a chip or a source adds it to the canvas, linked to the card
  by an **Answer** edge (placed up and to the left); with no card it goes to the middle of the
  screen.
- **Pin** puts the answer on the canvas as a note under the card (question on top, the cited
  sources listed as `[A2] title (holder)`), linked by an Answer edge. Notes now have hidden
  handles so edges can reach them, and show their saved text (`defaultValue`).
- Answers are cached on the server, so asking the same thing again is instant.
