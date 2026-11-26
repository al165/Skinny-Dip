import { Controls, MiniMap, Panel, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, type DragEvent } from "react";

import type { Item } from "./api";
import { RelationEdge } from "./edges/RelationEdge";
import { AnswerNode } from "./nodes/AnswerNode";
import { ImageNode } from "./nodes/ImageNode";
import { NoteNode } from "./nodes/NoteNode";
import { PassageNode } from "./nodes/PassageNode";
import { QuestionNode } from "./nodes/QuestionNode";
import { AskPanel } from "./panels/AskPanel";
import { ResultsPanel } from "./panels/ResultsPanel";
import { SEED_DRAG_TYPE, SeedsPanel } from "./panels/SeedsPanel";
import { useGraph } from "./store/graphs";

const nodeTypes = { passage: PassageNode, note: NoteNode, image: ImageNode, question: QuestionNode, answer: AnswerNode };
const edgeTypes = { relation: RelationEdge };

/** Notes get their own drag type so the drop handler can tell them apart from seeds. */
const NOTE_DRAG_TYPE = "application/x-dip-note";

function Canvas() {
    const loadSeeds = useGraph(s => s.loadSeeds);
    useEffect(() => { loadSeeds(); }, [loadSeeds]);

    const nodes = useGraph(s => s.nodes);
    const edges = useGraph(s => s.edges);
    const onNodesChange = useGraph(s => s.onNodesChange);
    const onEdgesChange = useGraph(s => s.onEdgesChange);
    const addSeed = useGraph(s => s.addSeed);
    const addNote = useGraph(s => s.addNote);
    const clear = useGraph(s => s.clear);
    const setSideOpen = useGraph(s => s.setSideOpen);
    const { screenToFlowPosition } = useReactFlow();

    const onDragNoteStart = (e: DragEvent) => {
        e.dataTransfer.setData(NOTE_DRAG_TYPE, "note");
        e.dataTransfer.effectAllowed = "move";
    };

    const onDragOver = useCallback((e: DragEvent) => {
        const types = e.dataTransfer.types;
        if (!types.includes(SEED_DRAG_TYPE) && !types.includes(NOTE_DRAG_TYPE)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
    }, []);

    const onDrop = useCallback((e: DragEvent) => {
        const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        if (e.dataTransfer.types.includes(NOTE_DRAG_TYPE)) {
            e.preventDefault();
            addNote(position);
            return;
        }
        const raw = e.dataTransfer.getData(SEED_DRAG_TYPE);
        if (!raw) return;
        e.preventDefault();
        addSeed(JSON.parse(raw) as Item, position);
        setSideOpen(false);
    }, [addSeed, addNote, setSideOpen, screenToFlowPosition]);

    return (
        <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onDragOver={onDragOver}
            onDrop={onDrop}
            nodesConnectable={false}
            colorMode="system"
            minZoom={0.5}
            maxZoom={5}
        >
            <Controls />
            <MiniMap pannable zoomable />
            <SeedsPanel />
            <ResultsPanel />
            <AskPanel />
            <Panel id="canvas-buttons" position="top-right">
                {nodes.length > 0 && <button className="clear" onClick={clear} title="Clear the canvas">clear</button>}
                <button className="note" draggable onDragStart={onDragNoteStart} title="Drag to add a text note">+ note</button>
            </Panel>
        </ReactFlow>
    );
}

export default function App() {
    return (
        <div id="flow-container" style={{ height: "100%", width: "100%" }}>
            <ReactFlowProvider>
                <Canvas />
            </ReactFlowProvider>
        </div>
    );
}
