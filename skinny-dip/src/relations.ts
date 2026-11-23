import type { EdgeMarker, XYPosition } from "@xyflow/react";
import type { Relation } from "./api";

type RelationStyle = {
    /** Text of the edge's label; null/absent = no label. */
    label?: string | null,
    colour: string,
    hint: string,
    /** Which side of the parent the new node goes, and which handles the edge uses. */
    sourceHandle: string,
    targetHandle: string,
    place: (parent: XYPosition, size: { w: number, h: number }, n: number) => XYPosition,
    stroke?: string,
    strokeWidth?: number,
    /** SVG dash pattern for the edge line, e.g. "2 6" for dots (with a round line cap). */
    strokeDasharray?: string,
    /** Overrides for the arrow head at the target end (merged over the default MarkerType.Arrow, 20×20,
     *  var(--arrow)), e.g. { type: MarkerType.ArrowClosed } or { width: 12, height: 12 }; null = no arrow head. */
    markerProps?: Partial<EdgeMarker> | null,
};

const W = 280;      // node width (keep in sync with .passage-node in index.css)
const GAP = 120;

// 0, +1, -1, +2, -2, … so siblings fan out around the parent's axis.
const spread = (n: number) => (n === 0 ? 0 : n % 2 ? (n + 1) / 2 : -n / 2);

export const RELATION_STYLE: Record<Relation, RelationStyle> = {
    "neighbours": {
        label: "Neighbours", colour: "#f27ff2", hint: "Similar passages",
        sourceHandle: "s-right", targetHandle: "t-left",
        place: (p, s, n) => ({ x: p.x + s.w + GAP, y: p.y + spread(n) * 260 }),
    },
    "counter": {
        label: "Counter", colour: "#6dbd2c", hint: "A passage that pushes back",
        sourceHandle: "s-left", targetHandle: "t-right",
        place: (p, _s, n) => ({ x: p.x - W - GAP, y: p.y + spread(n) * 260 }),
    },
    "zoom-out": {
        label: "Zoom out", colour: "#424dfd", hint: "The broader idea this sits in",
        sourceHandle: "s-top", targetHandle: "t-bottom",
        place: (p, _s, n) => ({ x: p.x + spread(n) * (W + 40), y: p.y - 300 }),
    },
    "zoom-in": {
        label: "Zoom in", colour: "#ff8800", hint: "More specific detail",
        sourceHandle: "s-bottom", targetHandle: "t-top",
        place: (p, s, n) => ({ x: p.x + spread(n) * (W + 40), y: p.y + s.h + GAP }),
    },
    "images": {
        label: "Images", colour: "#e0569b", hint: "A picture that illustrates it (by subject or metaphor)",
        sourceHandle: "s-right", targetHandle: "t-left",
        place: (p, s, n) => ({ x: p.x + s.w + GAP, y: p.y - 120 + spread(n) * 240 }),
    },
    "image-passages": {
        label: "Arguments", colour: "#2a8c8c", hint: "Arguments this image fits",
        sourceHandle: "s-bottom", targetHandle: "t-top",
        place: (p, s, n) => ({ x: p.x + spread(n) * (W + 40), y: p.y + s.h + GAP }),
    },
    "shared-with": {
        label: "Shared with", colour: "#d4a017", hint: "The same view, held by someone else",
        sourceHandle: "s-right", targetHandle: "t-left",
        place: (p, s, n) => ({ x: p.x + s.w + GAP, y: p.y - 200 + spread(n) * 260 }),
    },
    "sources": {
        label: "Sources", colour: "#8a8a8a", hint: "The verbatim passages it was extracted from",
        sourceHandle: "s-bottom", targetHandle: "t-top",
        place: (p, s, n) => ({ x: p.x - W - GAP + spread(n) * 60, y: p.y + s.h + GAP + n * 40 }),
    },
    "arguments-here": {
        label: "Arguments here", colour: "#2a8c8c", hint: "Arguments extracted from this passage",
        sourceHandle: "s-top", targetHandle: "t-bottom",
        place: (p, _s, n) => ({ x: p.x + spread(n) * (W + 40), y: p.y - 300 }),
    },
    "holder": {
        label: "Holder", colour: "#c2185b", hint: "Other arguments by the same holder",
        sourceHandle: "s-right", targetHandle: "t-left",
        place: (p, s, n) => ({ x: p.x + s.w + GAP, y: p.y + 200 + spread(n) * 260 }),
    },
    "answer": {
        label: "Answer", colour: "#1f6feb", hint: "Ask the LLM a question about this card",
        sourceHandle: "s-left", targetHandle: "t-right",
        // up and to the left, two columns (Counter takes the left, Zoom out the top)
        place: (p, _s, n) => ({ x: p.x - (W + 40) * (1 + (n % 2)) - GAP + 40, y: p.y - 320 - Math.floor(n / 2) * 240 }),
    },
    "cites": {
        label: "Cites", colour: "#1f6feb", hint: "A source the answer cited",
        sourceHandle: "s-bottom", targetHandle: "t-top",
        place: (p, s, n) => ({ x: p.x + spread(n) * (W + 40), y: p.y + s.h + GAP }),
    },
    "description": {
        label: null, colour: "#3a5e48", hint: "The seed's description", strokeDasharray: "1 6",
        sourceHandle: "s-right", targetHandle: "t-left", markerProps: null,
        place: (p, s) => ({ x: p.x + s.w + 80, y: p.y }),
    },
    "evidence": {
        label: "Evidence", colour: "#904adb", hint: "Passages that support it",
        sourceHandle: "s-bottom", targetHandle: "t-top",
        place: (p, s, n) => ({ x: p.x + s.w + GAP + n * 40, y: p.y + s.h + GAP + n * 60 }),
    },
};

export const NODE_WIDTH = W;
