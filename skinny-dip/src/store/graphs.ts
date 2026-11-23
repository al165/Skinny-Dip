import {
    applyEdgeChanges, applyNodeChanges, MarkerType,
    type Edge, type Node, type OnEdgesChange, type OnNodesChange, type XYPosition,
} from "@xyflow/react";
import { create } from "zustand";

import { apiUrl, askQuestion, fetchNodes, runAction, type ActionResult, type Holder, type Item, type Kind, type Provenance, type QuestionResponse, type QuestionSource, type Relation, type SeedKind, type Source, runImagePassages } from "../api";
import { NODE_WIDTH, RELATION_STYLE } from "../relations";
import { flowId, IMAGE_Z, imageFlowId, randomRotation, resultFlowId, selectOnly } from "../utils";

export const NOTE_COLOURS = ["#4D4034", "#B56936", "#614956", "#254369", "#508C65", "#716994"];
const NOTE_SIZE = { width: 200, height: 70 };
const IMAGE_WIDTH = { width: 200 };

export type { Item } from "../api";

export type PassageData = {
    dipId: string,
    kind: Kind,
    title: string,
    text: string,
    rotation: number,
    isSeed?: boolean,
    role?: string | null,
    abstraction?: number | null,
    document?: string | null,
    /** URL of the original document. */
    sourceUrl?: string | null,
    section?: string | null,
    provenance?: Provenance,
    model?: string | null,
    holder?: Holder | null,
    reception?: { document: string, voice: string, url?: string | null }[],
    sources?: Source[],
    highlight?: Item["highlight"],
    /** Relation currently being fetched for this node, if any. */
    pending?: Relation | null,
    /** Last problem when expanding (empty results, offline fallback, server error). */
    notice?: string | null,
};
export type PassageNode = Node<PassageData, "passage">;

export type NoteData = {
    colour: string,
    text: string,
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

export type ImageData = {
    imageId: string,
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
export const isQuestion = (n: NodeType): n is QuestionNode => n.type === "question";
export const isAnswer = (n: NodeType): n is AnswerNode => n.type === "answer";


export type RelationEdgeData = { relation: Relation, why: string, score: number };
export type RelationEdge = Edge<RelationEdgeData, "relation">;

type Status = "idle" | "loading" | "ready" | "error";

export type Picker = {
    sourceId: string,
    relation: Relation,
    /** Key into `results`. */
    key: string,
    status: Status,
    notice: string | null,
};

/** The Ask panel: a question about a card (or, with no card, about the whole corpus). */
export type Ask = {
    /** Flow id of the card asked about; null = the whole corpus. */
    sourceId: string | null,
    /** Text selected in the card, if any: steers retrieval and is shown in the panel. */
    selection: string | null,
    status: Status,
    response: QuestionResponse | null,
    /** Flow id of the answer node the last response became. */
    answerId: string | null,
    error: string | null,
};

type Store = {
    nodes: NodeType[],
    edges: RelationEdge[],
    onNodesChange: OnNodesChange<NodeType>,
    onEdgesChange: OnEdgesChange<RelationEdge>,

    seedKind: SeedKind,
    seeds: Item[],
    seedsStatus: Status,
    seedsError: string | null,
    loadSeeds: (kind?: SeedKind) => Promise<void>,

    /** Action results per `${dipId}:${relation}`, cached so reopening the same action doesn't refetch. */
    results: Record<string, ActionResult[]>,
    /** The action whose results are shown in the results panel, or null when it's closed. */
    picker: Picker | null,
    /** Flow ids added per `${sourceFlowId}:${relation}`: what "added" means in the results panel and how
     *  siblings are placed. Kept apart from edges because links to image nodes are not drawn. */
    chosen: Record<string, string[]>,

    addSeed: (item: Item, position: XYPosition) => void,
    addNote: (position: XYPosition) => void,
    updateNote: (flowId: string, data: Partial<NoteData>) => void,
    /** Run an action on a node and open the results panel; nothing is added until `choose`. */
    expand: (flowId: string, relation: Relation) => Promise<void>,
    /** Add the chosen result to the canvas, linked to the picker's source node, and close the panel. */
    choose: (result: ActionResult) => void,
    closePicker: () => void,

    /** Whether the seeds panel on the left is expanded; it collapses when a seed is dropped or a question asked. */
    sideOpen: boolean,
    setSideOpen: (open: boolean) => void,

    ask: Ask | null,
    /** Ask about the whole corpus (from the seeds panel): opens the Ask panel for its status and sources. */
    askCorpus: (question: string, at: XYPosition) => Promise<void>,
    /** Open the Ask panel about a card (flow id) and a selection in it; null = the whole corpus. */
    openAsk: (sourceId: string | null, selection?: string | null) => void,
    /** Ask, then put the answer on the canvas as an answer node: linked from the card asked about, or,
     *  for a corpus question, from a new question node placed at `at`. */
    submitQuestion: (question: string, at: XYPosition) => Promise<void>,
    /** Add a source an answer drew on to the canvas, linked from that answer node by a Cites edge. */
    addCited: (answerId: string, result: QuestionSource) => void,
    closeAsk: () => void,
    removeNode: (flowId: string) => void,
    clear: () => void,
};


function toData(r: Item | ActionResult): PassageData {
    return {
        dipId: r.id,
        kind: r.kind as Kind,
        title: r.title,
        text: r.text,
        role: r.role ?? null,
        abstraction: r.abstraction ?? null,
        document: r.document ?? null,
        section: null,
        sourceUrl: r.source_url ?? null,
        provenance: r.provenance,
        model: r.model ?? null,
        holder: r.holder ?? null,
        reception: r.reception ?? [],
        sources: r.sources ?? [],
        highlight: r.highlight,
        rotation: randomRotation(),
    };
}

/** Run `relation` on a canvas node: image moves need an image, the others a DIP node. */
function fetchAction(source: PassageNode | ImageNode, relation: Relation) {
    if (relation === "image-passages") {
        if (!isImage(source)) throw new Error("Passages only applies to images");
        return runImagePassages(source.data.slug);
    }
    if (relation === "answer" || relation === "cites") throw new Error(`${relation} is not a move`);
    if (!isPassage(source)) throw new Error(`${relation} does not apply to images`);
    return runAction(relation, source.data.dipId);
}

function makeEdge(source: string, target: string, relation: Relation, r: { why: string, score: number }): RelationEdge {
    const style = RELATION_STYLE[relation];
    return {
        id: `${source}-${relation}-${target}`,
        source, target,
        sourceHandle: style.sourceHandle,
        targetHandle: style.targetHandle,
        type: "relation",
        data: { relation, why: r.why, score: r.score },
        markerEnd: { type: MarkerType.Arrow, color: 'var(--arrow)', height: 20, width: 20 },
        style: { stroke: 'var(--arrow)', strokeWidth: 2 },
    };
}

export const useGraph = create<Store>((set, get) => {
    // Narrow on `type` before touching `data`: inside each branch TS knows which data shape it has,
    // so the spread stays a valid PassageNode / NoteNode instead of a mix of both.
    const patchPassage = (id: string, data: Partial<PassageData>) =>
        set({ nodes: get().nodes.map((n): NodeType => n.id === id && isPassage(n) ? { ...n, data: { ...n.data, ...data } } : n) });
    const patchNote = (id: string, data: Partial<NoteData>) =>
        set({ nodes: get().nodes.map((n): NodeType => n.id === id && isNote(n) ? { ...n, data: { ...n.data, ...data } } : n) });
    const setPending = (id: string, pending: Relation | null) =>
        set({
            nodes: get().nodes.map((n): NodeType =>
                n.id !== id ? n
                    : isPassage(n) ? { ...n, data: { ...n.data, pending } }
                        : isImage(n) ? { ...n, data: { ...n.data, pending } } : n),
        });

    /** Add `result` to the canvas linked to `sourceId` by `relation` (or, with no source, at `at`),
     *  converging on the node if it's already there. Shared by the results panel and the Ask panel. */
    const link = (sourceId: string | null, relation: Relation, result: ActionResult, at: XYPosition | null) => {
        const { nodes, edges, chosen } = get();
        const parent = sourceId ? nodes.find(n => n.id === sourceId) : undefined;
        if (!parent && !at) return;

        const isPic = result.type === "image";
        const targetId = resultFlowId(result);
        const chosenKey = `${sourceId ?? "corpus"}:${relation}`;
        const linked = new Set(chosen[chosenKey] ?? []);
        if (linked.has(targetId)) {
            // Already chosen: just select it.
            set({ nodes: selectOnly(nodes, targetId) });
            return;
        }

        // No edges to or from image nodes: pictures sit next to what they illustrate, unlinked.
        const drawEdge = !!parent && !isPic && !isImage(parent);
        let newNodes: NodeType[];
        if (nodes.some(n => n.id === targetId)) {
            // Converge: link to the node that's already on the canvas and select it.
            newNodes = selectOnly(nodes, targetId);
        } else {
            const position = parent
                ? RELATION_STYLE[relation].place(parent.position,
                    { w: parent.measured?.width ?? NODE_WIDTH, h: parent.measured?.height ?? 200 }, linked.size)
                : { x: at!.x + (linked.size % 3) * (NODE_WIDTH + 40), y: at!.y + Math.floor(linked.size / 3) * 260 };
            const node: NodeType = isPic
                ? {
                    id: targetId, type: "image", position, selected: false, width: IMAGE_WIDTH.width, zIndex: IMAGE_Z,
                    data: {
                        imageId: result.slug ?? result.id, slug: result.slug ?? result.id, title: result.title,
                        description: result.text, url: apiUrl(result.url ?? ""), credit: result.credit ?? null,
                        license: result.license ?? null, sourceUrl: result.source_url ?? null, rotation: randomRotation(),
                    },
                }
                : { id: targetId, type: "passage", position, data: toData(result), selected: false };
            newNodes = [...selectOnly(nodes), node];
        }
        set({
            nodes: newNodes,
            edges: drawEdge ? [...edges, makeEdge(parent!.id, targetId, relation, result)] : edges,
            chosen: { ...chosen, [chosenKey]: [...linked, targetId] },
        });
    };

    /** Put an answer on the canvas, linked from the card asked about or, with no card, from a new question
     *  node at `at`. Returns the answer's flow id (an existing one if the same question was asked before). */
    const placeAnswer = (sourceId: string | null, selection: string | null, r: QuestionResponse, at: XYPosition): string | null => {
        const key = `${sourceId ?? "corpus"}|${selection ?? ""}|${r.question}`;
        const { nodes, edges } = get();
        const existing = nodes.find(n => isAnswer(n) && n.data.key === key);
        if (existing) {
            set({ nodes: selectOnly(nodes, existing.id) });
            return existing.id;
        }
        const card = sourceId ? nodes.find(n => n.id === sourceId) : undefined;
        if (sourceId && !card) return null;  // the card was removed while the question was out

        const answerId = `answer-${crypto.randomUUID()}`;
        const answer: AnswerNode = {
            id: answerId, type: "answer", position: { x: 0, y: 0 }, selected: true,
            data: {
                key, question: r.question, selection, answer: r.answer, found: r.found,
                sources: r.sources, model: r.meta.model, rotation: randomRotation(),
            },
        };
        const newNodes: NodeType[] = selectOnly(nodes);
        let edge: RelationEdge;
        if (card) {
            const n = nodes.filter(m => isAnswer(m) && edges.some(e => e.source === card.id && e.target === m.id)).length;
            answer.position = RELATION_STYLE.answer.place(card.position,
                { w: card.measured?.width ?? NODE_WIDTH, h: card.measured?.height ?? 200 }, n);
            edge = makeEdge(card.id, answerId, "answer", { why: r.question, score: 1 });
        } else {
            const questionId = `question-${crypto.randomUUID()}`;
            const q: QuestionNode = {
                id: questionId, type: "question", selected: false,
                position: { x: at.x - NODE_WIDTH / 2, y: at.y - 60 },
                data: { question: r.question, rotation: randomRotation() },
            };
            newNodes.push(q);
            answer.position = { x: q.position.x, y: q.position.y + 120 + 120 };
            edge = { ...makeEdge(questionId, answerId, "answer", { why: r.question, score: 1 }), sourceHandle: "s-bottom", targetHandle: "t-top" };
        }
        set({ nodes: [...newNodes, answer], edges: [...edges, edge] });
        return answerId;
    };

    return {
        nodes: [],
        edges: [],
        onNodesChange: (changes) => set({ nodes: applyNodeChanges(changes, get().nodes) }),
        onEdgesChange: (changes) => set({ edges: applyEdgeChanges(changes, get().edges) }),

        seedKind: "issue",
        seeds: [],
        seedsStatus: "idle",
        seedsError: null,
        loadSeeds: async (kind = get().seedKind) => {
            if (get().seedsStatus === "loading") return;
            set({ seedKind: kind, seedsStatus: "loading", seedsError: null });
            try {
                const seeds = await fetchNodes(kind);
                set({ seeds, seedsStatus: "ready" });
            } catch (e) {
                console.error(e);
                set({ seedsStatus: "error", seedsError: String(e instanceof Error ? e.message : e) });
            }
        },

        results: {},
        picker: null,
        chosen: {},

        addSeed: (item, position) => {
            console.log("Add Seed");
            console.log(item);
            if (item.role === "image") {
                // keyed by slug, like image results from the Images move, so both paths reach one node
                const id = imageFlowId(item.slug ?? item.id);
                const { nodes } = get();
                if (nodes.some(n => n.id === id)) {
                    set({ nodes: selectOnly(nodes, id) });
                    return;
                }
                const node: ImageNode = {
                    id, type: "image", position: { x: position.x - NODE_WIDTH / 2, y: position.y - 100 }, zIndex: IMAGE_Z,
                    data: {
                        imageId: item.slug ?? item.id, slug: item.slug ?? "", title: item.title, description: item.text,
                        url: item.url ?? "", credit: item.credit ?? null, license: item.license ?? null,
                        sourceUrl: item.source_url, rotation: randomRotation(),
                    },
                    selected: false,
                    width: IMAGE_WIDTH.width,
                };
                set({ nodes: [...selectOnly(nodes), node] });
                return;
            }

            const id = flowId(item.id);
            const { nodes } = get();
            if (nodes.some(n => n.id === id)) {
                // Already on the canvas: just select it.
                set({ nodes: selectOnly(nodes, id) });
                return;
            }
            const data = toData(item);
            data.isSeed = true;
            const node: PassageNode = {
                id, type: "passage", position: { x: position.x - NODE_WIDTH / 2, y: position.y - 40 },
                data, selected: false
            };
            set({ nodes: [...selectOnly(nodes), node] });
        },

        addNote: (position) => {
            const node: NoteNode = {
                id: `note-${crypto.randomUUID()}`, type: "note",
                position: { x: position.x - NOTE_SIZE.width / 2, y: position.y - NOTE_SIZE.height / 2 },
                // width/height make it resizable (NodeResizer updates them through onNodesChange).
                ...NOTE_SIZE,
                data: { colour: NOTE_COLOURS[0], text: "" }, selected: true,
            };
            set({ nodes: [...selectOnly(get().nodes), node] });
        },

        updateNote: patchNote,

        expand: async (sourceId, relation) => {
            const source = get().nodes.find(n => n.id === sourceId);
            if (!source || !(isPassage(source) || isImage(source)) || source.data.pending)
                return;
            set({ ask: null });  // one panel at a time

            // Flow ids are stable per DIP node / image, so they key the cache.
            const key = `${sourceId}:${relation}`;
            set({ picker: { sourceId, relation, key, status: "ready", notice: null } });
            const cached = get().results[key];
            if (cached) {
                if (cached.length === 1) get().choose(cached[0]);  // one result: add it, no panel
                return;
            }

            // Only update the picker if it still shows this request (the user may have opened another meanwhile).
            const patchPicker = (p: Partial<Picker>) => {
                const cur = get().picker;
                if (cur?.key === key && cur.sourceId === sourceId) set({ picker: { ...cur, ...p } });
            };

            patchPicker({ status: "loading" });
            setPending(sourceId, relation);
            if (isPassage(source)) patchPassage(sourceId, { notice: null });
            try {
                const res = await fetchAction(source, relation);
                const selfId = isPassage(source) ? source.data.dipId : null;
                const results = res.results.filter(r => (r.type === "node" || r.type === "image") && r.id !== selfId);
                set({ results: { ...get().results, [key]: results } });
                const cur = get().picker;
                if (results.length === 1 && cur?.key === key && cur.sourceId === sourceId) {
                    get().choose(results[0]);  // one result: add it straight away, no panel
                    return;
                }
                patchPicker({
                    status: "ready",
                    notice: results.length ? null : res.meta?.fallback_error
                        ? `No ${relation} results (${res.meta.fallback_error})`
                        : `No ${relation} results for this ${isPassage(source) ? source.data.kind : "image"}.`,
                });
            } catch (e) {
                patchPicker({ status: "error", notice: `${relation} failed: ${e instanceof Error ? e.message : e}` });
            } finally {
                setPending(sourceId, null);
            }
        },

        choose: (result) => {
            const picker = get().picker;
            if (!picker) return;
            link(picker.sourceId, picker.relation, result, null);
            set({ picker: null });
        },

        closePicker: () => set({ picker: null }),

        sideOpen: true,
        setSideOpen: (sideOpen) => set({ sideOpen }),

        ask: null,

        askCorpus: async (question, at) => {
            get().openAsk(null);
            await get().submitQuestion(question, at);
        },

        openAsk: (sourceId, selection = null) => {
            const cur = get().ask;
            // Reopening on the same card and selection keeps the last answer.
            if (cur && cur.sourceId === sourceId && cur.selection === selection) {
                set({ picker: null });
                return;
            }
            set({ picker: null, ask: { sourceId, selection: selection?.trim() || null, status: "idle", response: null, answerId: null, error: null } });
        },

        submitQuestion: async (question, at) => {
            const ask = get().ask;
            question = question.trim();
            if (!ask || ask.status === "loading" || !question) return;
            const source = ask.sourceId ? get().nodes.find(n => n.id === ask.sourceId) : undefined;
            const nodeId = source && isPassage(source) ? source.data.dipId : null;
            const same = (a: Ask | null) => a?.sourceId === ask.sourceId && a?.selection === ask.selection;
            set({ ask: { ...ask, status: "loading", error: null }, sideOpen: false });
            try {
                const response = await askQuestion(question, nodeId, ask.selection);
                const answerId = placeAnswer(ask.sourceId, ask.selection, response, at);
                if (same(get().ask)) set({ ask: { ...get().ask!, status: "ready", response, answerId } });
            } catch (e) {
                if (same(get().ask))
                    set({ ask: { ...get().ask!, status: "error", error: e instanceof Error ? e.message : String(e) } });
            }
        },

        addCited: (answerId, result) =>
            link(answerId, "cites", { ...result, why: `${result.why} · cited as [${result.label}]` }, null),

        closeAsk: () => set({ ask: null }),

        removeNode: (id) => set({
            nodes: get().nodes.filter(n => n.id !== id),
            edges: get().edges.filter(e => e.source !== id && e.target !== id),
            chosen: Object.fromEntries(Object.entries(get().chosen)
                .filter(([k]) => !k.startsWith(`${id}:`))
                .map(([k, v]) => [k, v.filter(t => t !== id)])),
            picker: get().picker?.sourceId === id ? null : get().picker,
            ask: get().ask?.sourceId === id ? null
                : get().ask?.answerId === id ? { ...get().ask!, answerId: null } : get().ask,
        }),

        clear: () => set({ nodes: [], edges: [], chosen: {}, picker: null, ask: null }),
    };
});
