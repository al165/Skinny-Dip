// Small pure helpers shared by the canvas components and the store: ids, labels, text rendering.
// No state here; the store (store/graphs.ts) and the API client (api.ts) import from this module,
// not the other way round (only types come back from them).
import { Position } from "@xyflow/react";
import { Fragment, type ReactNode } from "react";

import type { ActionResult, QuestionSource } from "./api";
import type { NodeType, PassageData } from "./store/graphs";

// --- canvas ids --------------------------------------------------------------------------

/** One canvas node per DIP node, so two paths that reach the same argument converge on it. */
export const flowId = (dipId: string) => `n${dipId}`;

/** Images are keyed by slug, a separate namespace from node ids, so they get their own prefix. */
export const imageFlowId = (slug: string) => `img${slug}`;

/** The canvas id an action result gets: images by slug, everything else by node id. */
export const resultFlowId = (r: ActionResult) => (r.type === "image" ? imageFlowId(r.slug ?? r.id) : flowId(r.id));

// --- canvas helpers ----------------------------------------------------------------------

/** Handles on every side of a node; each relation picks a source/target pair (see relations.ts). */
export const SIDES = [
    ["top", Position.Top],
    ["right", Position.Right],
    ["bottom", Position.Bottom],
    ["left", Position.Left],
] as const;

/** Copy of `nodes` with only `id` selected (or none). */
export const selectOnly = (nodes: NodeType[], id?: string): NodeType[] =>
    nodes.map(n => ({ ...n, selected: n.id === id }) as NodeType);

/** Image nodes sit behind cards and edges; xyflow adds 1000 to a selected node, which lifts it to the front. */
export const IMAGE_Z = -1;

const ROTATION_AMOUNT = 15;

/** A small random tilt (±10°) so cards on the canvas look placed by hand. */
export const randomRotation = () => Math.random() * ROTATION_AMOUNT - ROTATION_AMOUNT / 2;

// --- labels ------------------------------------------------------------------------------

export const KIND_LABEL: Record<string, string> = {
    argument: "argument", issue: "issue", theme: "theme", holder: "holder", passage: "passage",
};

/** How a document treats a view, as the start of a reception line ("rejected by the author of “…”"). */
export const VOICE_LABEL: Record<string, string> = {
    argued: "argued in", conceded: "conceded in", reported: "reported in", endorsed: "endorsed by the author of",
    rejected: "rejected by the author of",
};

/** What wrote the text: model summary, edited summary, or the source itself. */
export function provenanceLabel(d: Pick<PassageData, "provenance" | "document" | "kind" | "model">): string {
    if (d.provenance === "verbatim") return `Passage · verbatim${d.document ? ` from ${d.document}` : ""}`;
    if (d.provenance === "manual") return "Summary · edited";
    if (d.kind === "holder") return "Holder";
    return `Summary · written by ${d.model ?? "the LLM"}`;
}

// --- text --------------------------------------------------------------------------------

/** Passage text with the quote that led here highlighted (offsets from the quote check, else a plain search). */
export function withHighlight(text: string, h: PassageData["highlight"]): ReactNode {
    if (!h) return text;
    let start = h.start ?? -1, end = h.end ?? -1;
    if (start < 0 || end <= start || end > text.length) {
        start = text.indexOf(h.quote);
        end = start + h.quote.length;
    }
    if (start < 0) return text;
    return <>{text.slice(0, start)}<mark>{text.slice(start, end)}</mark>{text.slice(end)}</>;
}

const LABEL = /\[([A-Z]\d+)\]/g;

/** Answer text with each [A2]-style label as a chip that adds that source to the canvas. */
export function withCitations(answer: string, sources: QuestionSource[], add: (s: QuestionSource) => void): ReactNode {
    const byLabel = new Map(sources.map(s => [s.label, s]));
    const parts: ReactNode[] = [];
    let last = 0;
    for (const m of answer.matchAll(LABEL)) {
        const src = byLabel.get(m[1]);
        if (!src) continue;
        parts.push(<Fragment key={`t${last}`}>{answer.slice(last, m.index)}</Fragment>);
        parts.push(
            <button key={`c${m.index}`} className={`cite nodrag kind-${src.kind}`} onClick={() => add(src)}
                title={`${src.title}${src.holder ? ` · ${src.holder.name}` : ""}${src.document ? ` · ${src.document}` : ""}\nClick to add it to the canvas`}>
                {m[1]}
            </button>);
        last = m.index + m[0].length;
    }
    parts.push(<Fragment key="end">{answer.slice(last)}</Fragment>);
    return parts;
}
