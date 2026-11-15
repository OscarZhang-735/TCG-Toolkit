// Four-corner crop editor: an SVG laid over the photo in image-pixel coordinates. Corners
// can be dragged (mouse, pen, touch) or moved with the arrow keys; the server corrects the
// perspective, so any quadrilateral is allowed.
import {clampCorner} from './graded-cards.js';

const SVG = 'http://www.w3.org/2000/svg';
const HANDLE_PX = 11;  // on-screen handle radius, whatever the photo's size

function svgNode(tag, attributes = {}) {
  const element = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}

// Arrow-key step in image pixels: 0.5% of the longer side, 5x with Shift.
export const keyStep = (width, height, shift) => Math.max(width, height) * (shift ? 0.025 : 0.005);

export function createCropEditor(svg, {labels = ['', '', '', ''], onChange = () => {}} = {}) {
  let width = 1, height = 1, corners = [[0, 0], [1, 0], [1, 1], [0, 1]];
  const shade = svgNode('path', {class: 'graded-crop-shade', 'fill-rule': 'evenodd'});
  const outline = svgNode('polygon', {class: 'graded-crop-outline'});
  const handles = labels.map((label, index) => {
    const handle = svgNode('circle', {class: 'graded-crop-handle', tabindex: '0', role: 'button', 'aria-label': label});
    handle.addEventListener('pointerdown', event => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add('dragging');
    });
    handle.addEventListener('pointermove', event => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      move(index, toImage(event));
    });
    const release = event => { handle.classList.remove('dragging'); if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); };
    handle.addEventListener('pointerup', release);
    handle.addEventListener('pointercancel', release);
    handle.addEventListener('keydown', event => {
      const step = keyStep(width, height, event.shiftKey);
      const delta = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
      if (!delta) return;
      event.preventDefault();
      move(index, [corners[index][0] + delta[0], corners[index][1] + delta[1]]);
    });
    return handle;
  });
  svg.replaceChildren(shade, outline, ...handles);

  function toImage(event) {
    const box = svg.getBoundingClientRect();
    return [(event.clientX - box.left) / box.width * width, (event.clientY - box.top) / box.height * height];
  }
  function move(index, point) {
    corners[index] = clampCorner(point, width, height);
    draw(); onChange(corners);
  }
  function draw() {
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const scale = (svg.getBoundingClientRect().width || width) / width;
    outline.setAttribute('points', corners.map(point => point.join(',')).join(' '));
    shade.setAttribute('d', `M0 0H${width}V${height}H0Z M${corners.map(point => point.join(' ')).join(' L')}Z`);
    handles.forEach((handle, index) => {
      handle.setAttribute('cx', corners[index][0]); handle.setAttribute('cy', corners[index][1]);
      handle.setAttribute('r', HANDLE_PX / scale);
    });
  }
  return {
    set(nextWidth, nextHeight, nextCorners) {
      width = nextWidth; height = nextHeight;
      corners = nextCorners.map(point => clampCorner(point, width, height));
      draw();
    },
    corners: () => corners.map(point => [...point]),
    // Handle size depends on the displayed size; call once the dialog has laid out.
    redraw: draw,
    setLabels(next) { handles.forEach((handle, index) => handle.setAttribute('aria-label', next[index])); },
  };
}
