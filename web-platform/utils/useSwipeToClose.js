import { useCallback, useRef, useState } from 'react';

// Bind the returned `handlers` to a drag-handle element (e.g. the modal
// header) - not the scrollable content - so it doesn't fight with content
// scrolling or chart pan/zoom gestures. Apply `style` to the outer panel
// so it visually follows the finger while dragging and closes once pulled
// past `threshold` px.
//
// Pass `scrollRef` to instead bind `handlers` across a much larger area
// (e.g. the whole modal, header + content). When provided, a downward drag
// only starts the close gesture if the referenced scrollable element is
// already scrolled to the top (scrollTop <= 0) - the standard "pull to
// dismiss" pattern used by native bottom sheets. This lets the same touch
// region handle both "scroll the content" and "swipe down to close"
// without the two fighting each other.
export function useSwipeToClose(onClose, threshold = 90, scrollRef = null) {
  const startY = useRef(null);
  const startX = useRef(null);
  const armed = useRef(true);
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);

  const onTouchStart = useCallback((e) => {
    startY.current = e.touches[0].clientY;
    startX.current = e.touches[0].clientX;
    armed.current = !scrollRef || !scrollRef.current || scrollRef.current.scrollTop <= 0;
    setDragging(true);
  }, [scrollRef]);

  const onTouchMove = useCallback((e) => {
    if (startY.current == null || !armed.current) return;
    const dy = e.touches[0].clientY - startY.current;
    const dx = e.touches[0].clientX - startX.current;
    // Only treat it as a close-swipe if the motion is mostly vertical/down
    if (dy > 0 && dy > Math.abs(dx)) setDragY(dy);
    else if (dy < 0) armed.current = false; // scrolling content back up - hand off to native scroll
  }, []);

  const onTouchEnd = useCallback(() => {
    setDragging(false);
    if (dragY > threshold) {
      onClose();
    }
    setDragY(0);
    startY.current = null;
    startX.current = null;
  }, [dragY, threshold, onClose]);

  return {
    handlers: { onTouchStart, onTouchMove, onTouchEnd },
    panelStyle: {
      transform: dragY ? `translateY(${dragY}px)` : undefined,
      transition: dragging ? 'none' : 'transform 0.2s ease-out',
    },
    dragY,
  };
}
