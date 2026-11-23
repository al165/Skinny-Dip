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

## Source layout

```
src/
  main.tsx, App.tsx    entry; the canvas, node/edge types and drop handling
  api.ts               HTTP client and the API's shapes; MOVES per kind
  types.ts             canvas node/edge types (PassageData, AnswerNode, Relation…) and type guards
  relations.ts         per-relation style: colour, label, handles, line/arrow style, placement
  store/graphs.ts      the zustand store: nodes, edges, seeds, move results, results/Ask panels
  store/build.ts       node and edge constructors, and the call behind each move
  nodes/               PassageNode, AnswerNode, QuestionNode, ImageNode, NoteNode; parts.tsx is
                       shared pieces (handles, tilt, remove button, moves toolbar)
  edges/               RelationEdge, FloatingConnectionLine; floating.ts places edge ends on tilted cards
  panels/              SeedsPanel (seeds + corpus question), ResultsPanel, AskPanel, ResultCard
  lib/                 canvas ids (canvas.ts), text rendering (text.tsx), small UI hooks (hooks.ts)
```

## What's different from dip-memory's skinny-dip

- **Seed tabs:** Themes, Issues, Arguments, Holders, Images (`panels/SeedsPanel.tsx`). The filter
  also matches holder names.
- **Seeds:** a dropped seed is a title card; its **+** button adds a description card beside it
  (dotted edge). Both carry the same DIP node (ids `n<id>-title` / `n<id>-text`, see
  `lib/canvas.ts`), so moves and Ask work the same on either and share cached results.
- **Node kinds:** argument, issue, theme, holder, passage, plus image nodes (`api.ts`). Ids are
  strings (`a…`, `i…`, `t…`, `p<n>`, `h:<slug>`; images by slug).
- **Moves per kind** (`MOVES` in `api.ts`; styles in `relations.ts`):
  - argument: Counter, Evidence, Neighbours, Zoom in/out, Shared with, Sources, Holder, Images
  - issue: Zoom in/out, Neighbours, Images
  - theme: Zoom in, Neighbours, Images
  - holder: Holder
  - passage: Arguments here, Images
  - image: Arguments (the reverse search)
- **Card rendering** (`nodes/PassageNode.tsx`):
  - holder chip, coloured by holder type
  - reception lines ("rejected by the author of “…”"), linked to each document's URL
  - the quotes, in a collapsible block when selected; an unverified quote is marked ⚠
  - passages in a serif face with a dashed border, the quote that led to them highlighted
  - a small random tilt; edges meet the tilted outline (`edges/floating.ts`)
- **Results panel:** shows the holder, a verbatim style for passages, and image thumbnails with
  their credit. Choosing an image result adds an image node.
- Credits on images are always shown (tooltip on the node, line in the results).

## Ask (questions to the LLM)

`panels/AskPanel.tsx`, state in `store/graphs.ts` (`ask`, `openAsk`, `submitQuestion`, `askCorpus`,
`addCited`), API call `askQuestion` in `api.ts` (`POST /question`, see `../README.md`).

- **Ask about a card:** every card's toolbar has an Ask button.
- **Ask about a part of it:** a selected card's text can be selected (the text is `nodrag` while
  the card is selected); an "Ask about “…”" button appears above the card and the panel quotes
  the selection. The selection is read from `selectionchange`, only inside that card's text.
- **Ask the whole corpus:** the box at the top of the seeds panel. The question becomes a question
  node in the middle of the screen, with its answer below it.
- **The answer** goes on the canvas as an answer node, linked from the card by an **Answer** edge
  (up and to the left). Its citation chips (`[A2]` …) add that source, linked by a **Cites** edge.
- **The panel** (same slot as the results panel; opening one closes the other): question box
  (Enter sends, Shift+Enter is a new line), then every source used, cited first; clicking one adds
  it to the canvas like a chip.
- Answers are cached on the server, so asking the same thing again is instant.
