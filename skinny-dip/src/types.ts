// Canvas types: the node and edge shapes React Flow renders, and their data.
// API shapes (what the server returns) live in api.ts.
import type { Edge, Node } from "@xyflow/react";

import type { ImageRelation, Item, Kind, PassageRelation, Provenance, QuestionSource, Source, Holder } from "./api";

/** Everything an edge can stand for: the API moves, plus three links made on the canvas:
 *  "answer" (card or corpus question → its answer), "cites" (answer → a source it cited) and
 *  "description" (seed title → its description card). */
export type Relation = PassageRelation | ImageRelation | "answer" | "cites" | "description";

// --- nodes -------------------------------------------------------------------------------

/** A DIP node (argument, issue, theme, holder or passage) as a card. */
export type PassageData = {
    dipId: string,
    kind: Kind,
    title: string,
    text: string,
    rotation: number,
    /** Dragged in from the seeds panel (seed styling). */
    isSeed?: boolean,
    /** The title half of a seed (`seedTitleId`): shows only the title, and can expand its description. */
    isSeedTitle?: boolean,
    /** The description half of a seed (`seedTextId`): shows `text` without the title. */
    isSeedText?: boolean,
    role?: string | null,
    abstraction?: number | null,
    document?: string | null,
    /** URL of the original document. */
    sourceUrl?: string | null,
    provenance?: Provenance,
    model?: string | null,
    holder?: Holder | null,
    reception?: Item["reception"],
    sources?: Source[],
    highlight?: Item["highlight"],
    /** Relation currently being fetched for this node, if any. */
    pending?: Relation | null,
    /** Last problem when expanding (empty results, offline fallback, server error). */
    notice?: string | null,
};
export type PassageNode = Node<PassageData, "passage">;

/** A free text note, added from the "+ note" button. */
export type NoteData = {
    colour: string,
    text: string,
    /** Text size in px; absent on notes made before sizes existed (falls back to NOTE_DEFAULT_SIZE). */
    fontSize?: number,
    /** Whole-note styles (a textarea can't style a selection); absent = off. */
    bold?: boolean,
    italic?: boolean,
};
export type NoteNode = Node<NoteData, "note">;

/** A question asked about the whole corpus: the root its answer stems from. */
export type QuestionData = {
    question: string,
    rotation: number,
};
export type QuestionNode = Node<QuestionData, "question">;

/** An LLM answer, linked from the card (or question node) it answers; its citations add sources linked from it. */
export type AnswerData = {
    /** `${sourceId}|${selection}|${question}`: asking the same thing again selects this node. */
    key: string,
    question: string,
    selection: string | null,
    answer: string,
    found: boolean,
    sources: QuestionSource[],
    model: string,
    rotation: number,
};
export type AnswerNode = Node<AnswerData, "answer">;

/** A picture from the image library. */
export type ImageData = {
    slug: string,
    title: string,
    description: string,
    /** Absolute URL of the picture. */
    url: string,
    credit: string | null,
    license: string | null,
    /** Where the image comes from. */
    sourceUrl: string | null,
    rotation: number,
    /** Relation currently being fetched for this node, if any. */
    pending?: Relation | null,
};
export type ImageNode = Node<ImageData, "image">;

export type NodeType = PassageNode | NoteNode | ImageNode | QuestionNode | AnswerNode;

// Type guards. `n.type === "passage"` narrows under TS 7 (tsc -b) but not under TS 6 (vtsls in the editor),
// because xyflow's Node is an intersection with a conditional type. Guards narrow on both.
export const isPassage = (n: NodeType): n is PassageNode => n.type === "passage";
export const isNote = (n: NodeType): n is NoteNode => n.type === "note";
export const isImage = (n: NodeType): n is ImageNode => n.type === "image";
export const isAnswer = (n: NodeType): n is AnswerNode => n.type === "answer";

// --- edges -------------------------------------------------------------------------------

export type RelationEdgeData = { relation: Relation, why: string, score: number };
export type RelationEdge = Edge<RelationEdgeData, "relation">;
