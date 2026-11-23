import { useCallback, useEffect, type DragEvent } from "react";

import {
    ReactFlow, ReactFlowProvider, Controls, MiniMap, Panel, useReactFlow,
} from "@xyflow/react";
import '@xyflow/react/dist/style.css'

import { PassageNode } from "./PassageNode";
import { RelationEdge } from "./RelationEdge";
import FloatingConectionLine from './FloatingConnectionLine';
import { DRAG_TYPE, SeedPanel } from "./SeedsPanel";
import { useGraph, type Item } from "./store/graphs";
import { NoteNode } from "./NoteNode";
import { ImageNode } from "./ImageNode";
import { ResultsPanel } from "./ResultsPanel";
import { AskPanel } from "./AskPanel";
import { AnswerNode, QuestionNode } from "./AnswerNode";

const nodeTypes = { passage: PassageNode, note: NoteNode, image: ImageNode, question: QuestionNode, answer: AnswerNode };
const NOTE_DRAG_TYPE = "application/x-dip-note";
const edgeTypes = { relation: RelationEdge };

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


    // Notes get their own drag type so onDrop can tell them apart from passages.
    const onDragNoteStart = (e: DragEvent) => {
        e.dataTransfer.setData(NOTE_DRAG_TYPE, "note");
        e.dataTransfer.effectAllowed = "move";
    };

    const onDragOver = useCallback((e: DragEvent) => {
        const types = e.dataTransfer.types;
        if (!types.includes(DRAG_TYPE) && !types.includes(NOTE_DRAG_TYPE)) return;
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

        const raw = e.dataTransfer.getData(DRAG_TYPE);
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
            connectionLineComponent={FloatingConectionLine}
            colorMode="system"
            minZoom={0.5}
            maxZoom={5}
        >
            {/* <Background /> */}
            <Controls />
            <MiniMap pannable zoomable />
            <SeedPanel />
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
        <div id="flow-container" style={{ height: '100%', width: '100%' }}>
            <ReactFlowProvider>
                <Canvas />
            </ReactFlowProvider>
        </div>
    );
}
