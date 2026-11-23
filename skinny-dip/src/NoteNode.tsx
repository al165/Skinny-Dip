import { NodeResizer, NodeToolbar, Position, type NodeProps } from "@xyflow/react";

import { NOTE_COLOURS, useGraph, type NoteNode as NoteNodeType } from "./store/graphs";
import { useEffect, useRef } from "react";


export function NoteNode({ id, selected, dragging, data }: NodeProps<NoteNodeType>) {
    const textRef = useRef<HTMLTextAreaElement>(null);

    const updateNote = useGraph(s => s.updateNote);
    const removeNode = useGraph(s => s.removeNode);

    const onBlur = (id: string, data: { text: string }) => {
        if (data.text.trim().length > 0)
            updateNote(id, data);
        else
            removeNode(id);
    }

    useEffect(() => {
        if (!data.text) textRef.current?.focus({ preventScroll: true });
    }, []);

    return (
        <>
            <NodeResizer isVisible={selected && !dragging} minWidth={120} minHeight={70} />

            <NodeToolbar position={Position.Top} align="start">
                <div className="note-toolbar nodrag">
                    {NOTE_COLOURS.map(c => (
                        <button
                            key={c}
                            className={`swatch${c === data.colour ? " active" : ""}`}
                            style={{ background: c }}
                            title={c}
                            onClick={() => updateNote(id, { colour: c })}
                        />
                    ))}
                    <button className="remove" title="Delete note" onClick={() => removeNode(id)}>×</button>
                </div>
            </NodeToolbar>

            <div className="note-node" >
                <textarea
                    defaultValue={data.text}
                    ref={textRef}
                    className="nodrag"
                    spellCheck="false"
                    style={{ color: data.colour }}
                    placeholder="Type your note here"
                    onChange={() => { }}
                    onBlur={e => onBlur(id, { text: e.target.value })}
                />
            </div>
        </>
    );
}
