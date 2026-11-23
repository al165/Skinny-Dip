// Pieces shared by the node components.
import { Handle, NodeToolbar, Position } from "@xyflow/react";
import type { ReactNode } from "react";

import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import type { Relation } from "../types";

const SIDES = [
    ["top", Position.Top],
    ["right", Position.Right],
    ["bottom", Position.Bottom],
    ["left", Position.Left],
] as const;

/** Invisible source and target handles on every side, for edge routing only (nodes aren't connectable by hand);
 *  each relation picks a pair (see relations.ts). */
export function Handles() {
    return SIDES.flatMap(([side, pos]) => [
        <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} isConnectable={false} />,
        <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} isConnectable={false} />,
    ]);
}

/** The hand-placed tilt of a card; edges account for it (edges/floating.ts). */
export function Rotated({ deg, children }: { deg: number, children: ReactNode }) {
    return <div className="rotation-container" style={{ transform: `rotate(${deg}deg)` }}>{children}</div>;
}

export function RemoveButton({ id, title = "Remove from canvas" }: { id: string, title?: string }) {
    const removeNode = useGraph(s => s.removeNode);
    return <button className="remove nodrag" title={title} onClick={() => removeNode(id)}>×</button>;
}

/** The move buttons under a node; `children` adds extra buttons (Ask) at the end. */
export function MovesToolbar({ id, moves, pending, children }:
    { id: string, moves: readonly Relation[], pending?: Relation | null, children?: ReactNode }) {
    const expand = useGraph(s => s.expand);
    return (
        <NodeToolbar position={Position.Bottom} align="center">
            <div className="actions nodrag">
                {moves.map(r => {
                    const style = RELATION_STYLE[r];
                    return (
                        <button key={r} title={style.hint} disabled={!!pending} className={pending === r ? "pending" : ""}
                            style={accent(style.colour)} onClick={() => expand(id, r)}>
                            {style.label}
                        </button>
                    );
                })}
                {children}
            </div>
        </NodeToolbar>
    );
}
