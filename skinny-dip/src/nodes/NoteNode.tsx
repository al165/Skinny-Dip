import { NodeResizer, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { useEffect, useLayoutEffect, useRef, useState, type FocusEvent } from "react";

import { NOTE_COLOURS, NOTE_DEFAULT_SIZE, NOTE_FONT_SIZES } from "../store/build";
import { useGraph } from "../store/graphs";
import type { NoteNode as NoteNodeType } from "../types";
import { RemoveButton } from "./parts";

const NOTE_PADDING = 20;

/** A swatch button that opens a menu of note colours. Closes on a pick, Escape or a click outside;
 *  it lives in the NodeToolbar, so it also unmounts (and closes) when the note is deselected. */
function ColourMenu({ colour, onPick }: { colour: string, onPick: (colour: string) => void }) {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
        document.addEventListener("pointerdown", onDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("pointerdown", onDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [open]);

    return (
        // preventDefault on mousedown keeps focus in the note's textarea (Safari never focuses buttons
        // on click, so relying on relatedTarget alone would read a click here as leaving the note).
        <div className="colour-menu" ref={ref} onMouseDown={e => e.preventDefault()}>
            <button className="colour-toggle" title="Text colour" aria-haspopup="true" aria-expanded={open}
                onClick={() => setOpen(o => !o)}>
                <span className="swatch" style={{ background: colour }} />
                <span className="caret">▾</span>
            </button>
            {open && (
                <div className="colour-options" role="menu">
                    {NOTE_COLOURS.map(c => (
                        <button key={c} role="menuitem" className={`swatch${c === colour ? " active" : ""}`}
                            style={{ background: c }} title={c}
                            onClick={() => { onPick(c); setOpen(false); }} />
                    ))}
                </div>
            )}
        </div>
    );
}

/** A free text note. A new note takes focus; leaving it empty deletes it. */
export function NoteNode({ id, selected, dragging, data }: NodeProps<NoteNodeType>) {
    const textRef = useRef<HTMLTextAreaElement>(null);
    const updateNote = useGraph(s => s.updateNote);
    const removeNode = useGraph(s => s.removeNode);
    const growNote = useGraph(s => s.growNote);
    const fontSize = data.fontSize ?? NOTE_DEFAULT_SIZE;

    // Grow the note when its text overflows. scrollHeight never reports less than the visible
    // height, so this only ever grows; NOTE_PADDING is the .note-node padding (top + bottom).
    const fit = () => {
        const ta = textRef.current;
        if (ta) growNote(id, Math.ceil(ta.scrollHeight) + NOTE_PADDING);
    };
    // Refit after a size or style change has been rendered (bold text is wider, so it can wrap onto
    // more lines), and once on mount, for notes loaded already full.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useLayoutEffect(fit, [fontSize, data.bold, data.italic]);

    // Focus only when the note is first added (empty), not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { if (!data.text) textRef.current?.focus({ preventScroll: true }); }, []);

    // The textarea and its toolbar count as one: moving focus between them (opening the size or
    // colour menu) isn't leaving the note, so an empty note only goes when focus leaves both.
    const toolbarRef = useRef<HTMLDivElement>(null);
    const onFocusLeave = (e: FocusEvent) => {
        const next = e.relatedTarget as Node | null;
        if (next && (textRef.current?.contains(next) || toolbarRef.current?.contains(next))) return;
        const text = textRef.current?.value ?? "";
        if (text.trim()) updateNote(id, { text });
        else removeNode(id);
    };

    return (
        <>
            <NodeResizer isVisible={selected && !dragging} minWidth={120} minHeight={70} />

            <NodeToolbar position={Position.Top} align="start">
                <div className="note-toolbar nodrag" ref={toolbarRef} onBlur={onFocusLeave}>
                    <ColourMenu colour={data.colour} onPick={colour => updateNote(id, { colour })} />
                    <select className="size" title="Text size" value={fontSize}
                        onChange={e => {
                            updateNote(id, { fontSize: Number(e.target.value) });
                            textRef.current?.focus({ preventScroll: true }); // carry on typing
                        }}>
                        {NOTE_FONT_SIZES.map(s => <option key={s} value={s}>{s}px</option>)}
                    </select>
                    {/* preventDefault on mousedown keeps focus (and the caret) in the textarea */}
                    <button className={`style-toggle bold${data.bold ? " active" : ""}`} title="Bold (Ctrl+B)" aria-pressed={!!data.bold}
                        onMouseDown={e => e.preventDefault()} onClick={() => updateNote(id, { bold: !data.bold })}>B</button>
                    <button className={`style-toggle italic${data.italic ? " active" : ""}`} title="Italic (Ctrl+I)" aria-pressed={!!data.italic}
                        onMouseDown={e => e.preventDefault()} onClick={() => updateNote(id, { italic: !data.italic })}>I</button>
                    <RemoveButton id={id} title="Delete note" />
                </div>
            </NodeToolbar>

            <div className="note-node">
                <textarea
                    ref={textRef}
                    defaultValue={data.text}
                    className="nodrag"
                    spellCheck="false"
                    style={{
                        color: data.colour, fontSize,
                        fontWeight: data.bold ? 700 : undefined,
                        fontStyle: data.italic ? "italic" : undefined,
                    }}
                    placeholder="Type your note here"
                    onInput={fit}
                    onKeyDown={e => {
                        // Ctrl+B / Ctrl+I (Cmd on macOS) toggle the whole-note styles, like the toolbar buttons.
                        if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
                        const key = e.key.toLowerCase();
                        if (key === "b") updateNote(id, { bold: !data.bold });
                        else if (key === "i") updateNote(id, { italic: !data.italic });
                        else return;
                        e.preventDefault(); // stop the browser's own shortcut (e.g. Firefox's Ctrl+B bookmarks sidebar)
                    }}
                    onBlur={onFocusLeave}
                />
            </div>
        </>
    );
}
