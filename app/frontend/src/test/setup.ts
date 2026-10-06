import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrolling
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => undefined;

// React Flow measures nodes with ResizeObserver / DOMMatrix, which jsdom does not have.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as any).ResizeObserver ??= ResizeObserverStub;
(globalThis as any).DOMMatrixReadOnly ??= class { m22 = 1; constructor(_?: string) {} };
