// Small UI hooks and handlers shared by the panels.
import { useReactFlow } from "@xyflow/react";
import { useEffect, type KeyboardEvent } from "react";

/** Call `onEscape` when Escape is pressed, while `active`. */
export function useEscape(active: boolean, onEscape: () => void) {
    useEffect(() => {
        if (!active) return;
        const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onEscape(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [active, onEscape]);
}

/** Where a corpus question's node goes: the middle of the screen, a third of the way down, in flow coordinates. */
export function useQuestionSpot() {
    const { screenToFlowPosition } = useReactFlow();
    return () => screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 3 });
}

/** A textarea keydown handler: Enter sends, Shift+Enter adds a line. */
export const sendOnEnter = (send: () => void) => (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
    }
};
