import { NodeResizer, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { useEffect, useRef } from "react";

import { NOTE_COLOURS } from "../store/build";
import { useGraph } from "../store/graphs";
import type { NoteNode as NoteNodeType } from "../types";
import { RemoveButton } from "./parts";

/** A free text note. A new note takes focus; leaving it empty deletes it. */
export function NoteNode({ id, selected, dragging, data }: NodeProps<NoteNodeType>) {
    const textRef = useRef<HTMLTextAreaElement>(null);
    const updateNote = useGraph(s => s.updateNote);
    const removeNode = useGraph(s => s.removeNode);

    // Focus only when the note is first added (empty), not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { if (!data.text) textRef.current?.focus({ preventScroll: true }); }, []);

    const onBlur = (text: string) => {
        if (text.trim()) updateNote(id, { text });
        else removeNode(id);
    };

    return (
        <>
            <NodeResizer isVisible={selected && !dragging} minWidth={120} minHeight={70} />

            <NodeToolbar position={Position.Top} align="start">
                <div className="note-toolbar nodrag">
                    {NOTE_COLOURS.map(c => (
                        <button key={c} className={`swatch${c === data.colour ? " active" : ""}`} style={{ background: c }} title={c}
                            onClick={() => updateNote(id, { colour: c })} />
                    ))}
                    <RemoveButton id={id} title="Delete note" />
                </div>
            </NodeToolbar>

            <div className="note-node">
                <textarea
                    ref={textRef}
                    defaultValue={data.text}
                    className="nodrag"
                    spellCheck="false"
                    style={{ color: data.colour }}
                    placeholder="Type your note here"
                    onBlur={e => onBlur(e.target.value)}
                />
            </div>
        </>
    );
}
