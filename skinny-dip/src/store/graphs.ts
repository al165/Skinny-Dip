// The canvas state (zustand): nodes and edges, the seeds list, cached move results, and the
// results and Ask panels. Node and edge constructors are in build.ts.
import { applyEdgeChanges, applyNodeChanges, type OnEdgesChange, type OnNodesChange, type XYPosition } from "@xyflow/react";
import { create } from "zustand";

import {
    apiUrl, askQuestion, fetchNodes,
    type ActionResult, type Item, type QuestionResponse, type QuestionSource, type SeedKind,
} from "../api";
import { flowId, imageFlowId, randomRotation, resultFlowId, seedTextId, seedTitleId, selectOnly, sizeOf } from "../lib/canvas";
import { NODE_WIDTH, RELATION_STYLE } from "../relations";
import {
    isAnswer, isImage, isNote, isPassage,
    type AnswerNode, type NodeType, type NoteData, type PassageData, type QuestionNode, type Relation, type RelationEdge,
} from "../types";
import { fetchAction, imageNode, makeEdge, noteNode, passageNode, toPassageData } from "./build";

type Status = "idle" | "loading" | "ready" | "error";

/** The results panel: the results of one move run on one node. */
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
    loadSeeds: (kind?: SeedKind, shuffled?: boolean) => Promise<void>,

    /** Move results per `${dipId}:${relation}` (images: `${flowId}:${relation}`), cached so reopening the same
     *  move doesn't refetch. */
    results: Record<string, ActionResult[]>,
    /** The move whose results are shown in the results panel, or null when it's closed. */
    picker: Picker | null,
    /** Flow ids added per `${sourceFlowId}:${relation}`: what "added" means in the results panel and how
     *  siblings are placed. Kept apart from edges because links to image nodes are not drawn. */
    chosen: Record<string, string[]>,

    /** Drop a seed on the canvas at `position`: an image, or a DIP node as a title card (default) or full card. */
    addSeed: (item: Item, position: XYPosition, asTitle?: boolean) => void,
    /** Show (or, if it's already there, remove) the description card of a seed title node. */
    toggleSeedText: (titleId: string) => void,
    addNote: (position: XYPosition) => void,
    updateNote: (flowId: string, data: Partial<NoteData>) => void,
    /** Raise a note's height to `height` px if it is shorter; never shrinks it. */
    growNote: (flowId: string, height: number) => void,
    /** Run a move on a node and open the results panel; nothing is added until `choose`. */
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

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export const useGraph = create<Store>((set, get) => {
    // Narrow on `type` before touching `data`: inside each branch TS knows which data shape it has,
    // so the spread stays a valid node of that type instead of a mix.
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

    /** Add `node` to the canvas, or, if a node with its id is already there, just select that one. */
    const addOrSelect = (node: NodeType) => {
        const { nodes } = get();
        set({ nodes: nodes.some(n => n.id === node.id) ? selectOnly(nodes, node.id) : [...selectOnly(nodes), node] });
    };

    /** Add `result` to the canvas linked from `sourceId` by `relation`, converging on the node if it's already
     *  there. Shared by the results panel and the answer nodes' citations. */
    const link = (sourceId: string, relation: Relation, result: ActionResult) => {
        const { nodes, edges, chosen } = get();
        const parent = nodes.find(n => n.id === sourceId);
        if (!parent) return;

        const isPic = result.type === "image";
        const targetId = resultFlowId(result);
        const chosenKey = `${sourceId}:${relation}`;
        const linked = chosen[chosenKey] ?? [];
        if (linked.includes(targetId)) {
            set({ nodes: selectOnly(nodes, targetId) });
            return;
        }

        let newNodes: NodeType[];
        if (nodes.some(n => n.id === targetId)) {
            // Converge: link to the node that's already on the canvas and select it.
            newNodes = selectOnly(nodes, targetId);
        } else {
            const position = RELATION_STYLE[relation].place(parent.position, sizeOf(parent), linked.length);
            const node = isPic
                ? imageNode(targetId, position, result, apiUrl(result.url ?? ""))
                : passageNode(targetId, position, toPassageData(result));
            newNodes = [...selectOnly(nodes), node];
        }
        // No edges to or from image nodes: pictures sit next to what they illustrate, unlinked.
        const drawEdge = !isPic && !isImage(parent);
        set({
            nodes: newNodes,
            edges: drawEdge ? [...edges, makeEdge(parent.id, targetId, relation, result)] : edges,
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
        const newNodes = selectOnly(nodes);
        const why = { why: r.question, score: 1 };
        let edge: RelationEdge;
        if (card) {
            const n = nodes.filter(m => isAnswer(m) && edges.some(e => e.source === card.id && e.target === m.id)).length;
            answer.position = RELATION_STYLE.answer.place(card.position, sizeOf(card), n);
            edge = makeEdge(card.id, answerId, "answer", why);
        } else {
            const question: QuestionNode = {
                id: `question-${crypto.randomUUID()}`, type: "question", selected: false,
                position: { x: at.x - NODE_WIDTH / 2, y: at.y - 60 },
                data: { question: r.question, rotation: randomRotation() },
            };
            newNodes.push(question);
            answer.position = { x: question.position.x, y: question.position.y + 240 };
            edge = { ...makeEdge(question.id, answerId, "answer", why), sourceHandle: "s-bottom", targetHandle: "t-top" };
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
        loadSeeds: async (kind = get().seedKind, shuffled = true) => {
            if (get().seedsStatus === "loading") return;
            set({ seedKind: kind, seedsStatus: "loading", seedsError: null });
            try {
                let seeds = await fetchNodes(kind);
                if (shuffled) {
                    for (let i = seeds.length - 1; i > 0; i--) {
                        const j = Math.floor(Math.random() * (i + 1));
                        [seeds[i], seeds[j]] = [seeds[j], seeds[i]];
                    }
                }
                set({ seeds, seedsStatus: "ready" });
            } catch (e) {
                console.error(e);
                set({ seedsStatus: "error", seedsError: errorText(e) });
            }
        },

        results: {},
        picker: null,
        chosen: {},

        addSeed: (item, position, asTitle = true) => {
            if (item.role === "image") {
                // Keyed by slug, like image results from the Images move, so both paths reach one node.
                // The library already gives an absolute URL.
                const at = { x: position.x - NODE_WIDTH / 2, y: position.y - 100 };
                addOrSelect(imageNode(imageFlowId(item.slug ?? item.id), at, item, item.url ?? ""));
                return;
            }
            const id = asTitle ? seedTitleId(item.id) : flowId(item.id);
            const data: PassageData = { ...toPassageData(item), isSeed: true, isSeedTitle: asTitle };
            addOrSelect(passageNode(id, { x: position.x - NODE_WIDTH / 2, y: position.y - 40 }, data));
        },

        toggleSeedText: (titleId) => {
            const { nodes, edges } = get();
            const title = nodes.find(n => n.id === titleId);
            if (!title || !isPassage(title) || !title.data.isSeedTitle) return;
            const textId = seedTextId(title.data.dipId);
            if (nodes.some(n => n.id === textId)) {
                get().removeNode(textId);
                return;
            }
            const position = RELATION_STYLE.description.place(title.position, sizeOf(title, 80), 0);
            // Same DIP node as the title (dipId, holder, sources…), so moves and Ask behave the same on both.
            const text = passageNode(textId, position,
                { ...title.data, isSeedTitle: false, isSeedText: true, pending: null, notice: null, rotation: randomRotation() });
            set({
                nodes: [...selectOnly(nodes), text],
                edges: [...edges, makeEdge(titleId, textId, "description", { why: "description", score: 1 })],
            });
        },

        addNote: (position) => set({ nodes: [...selectOnly(get().nodes), noteNode(position)] }),

        updateNote: patchNote,
        growNote: (id, height) => {
            const note = get().nodes.find(n => n.id === id);
            if (!note || !isNote(note) || (note.height ?? 0) >= height) return;
            set({ nodes: get().nodes.map((n): NodeType => n.id === id ? { ...n, height } : n) });
        },

        expand: async (sourceId, relation) => {
            const source = get().nodes.find(n => n.id === sourceId);
            if (!source || !(isPassage(source) || isImage(source)) || source.data.pending)
                return;

            // Results are a property of the DIP node, so passages key the cache by dipId: a seed's title and
            // description cards (two flow ids, one DIP node) share it. Images key it by flow id (one per slug).
            const key = `${isPassage(source) ? source.data.dipId : sourceId}:${relation}`;
            // One panel at a time.
            set({ ask: null, picker: { sourceId, relation, key, status: "ready", notice: null } });

            const cached = get().results[key];
            if (cached) {
                if (cached.length === 1) get().choose(cached[0]);  // one result: add it, no panel
                return;
            }

            // Only update the picker if it still shows this request (the user may have opened another meanwhile).
            const stillShown = () => {
                const cur = get().picker;
                return cur?.key === key && cur.sourceId === sourceId;
            };
            const patchPicker = (p: Partial<Picker>) => { if (stillShown()) set({ picker: { ...get().picker!, ...p } }); };

            patchPicker({ status: "loading" });
            setPending(sourceId, relation);
            if (isPassage(source)) patchPassage(sourceId, { notice: null });
            try {
                const res = await fetchAction(source, relation);
                const selfId = isPassage(source) ? source.data.dipId : null;
                const results = res.results.filter(r => (r.type === "node" || r.type === "image") && r.id !== selfId);
                set({ results: { ...get().results, [key]: results } });
                if (results.length === 1 && stillShown()) {
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
                patchPicker({ status: "error", notice: `${relation} failed: ${errorText(e)}` });
            } finally {
                setPending(sourceId, null);
            }
        },

        choose: (result) => {
            const picker = get().picker;
            if (!picker) return;
            link(picker.sourceId, picker.relation, result);
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
            // Only update the panel if it still shows this question's card and selection.
            const patchAsk = (p: Partial<Ask>) => {
                const cur = get().ask;
                if (cur?.sourceId === ask.sourceId && cur.selection === ask.selection) set({ ask: { ...cur, ...p } });
            };
            set({ ask: { ...ask, status: "loading", error: null }, sideOpen: false });
            try {
                const response = await askQuestion(question, nodeId, ask.selection);
                const answerId = placeAnswer(ask.sourceId, ask.selection, response, at);
                patchAsk({ status: "ready", response, answerId });
            } catch (e) {
                patchAsk({ status: "error", error: errorText(e) });
            }
        },

        addCited: (answerId, result) =>
            link(answerId, "cites", { ...result, why: `${result.why} · cited as [${result.label}]` }),

        closeAsk: () => set({ ask: null }),

        removeNode: (id) => {
            const { nodes, edges, chosen, picker, ask } = get();
            set({
                nodes: nodes.filter(n => n.id !== id),
                edges: edges.filter(e => e.source !== id && e.target !== id),
                chosen: Object.fromEntries(Object.entries(chosen)
                    .filter(([k]) => !k.startsWith(`${id}:`))
                    .map(([k, v]) => [k, v.filter(t => t !== id)])),
                picker: picker?.sourceId === id ? null : picker,
                ask: ask?.sourceId === id ? null : ask?.answerId === id ? { ...ask, answerId: null } : ask,
            });
        },

        clear: () => set({ nodes: [], edges: [], chosen: {}, picker: null, ask: null }),
    };
});
