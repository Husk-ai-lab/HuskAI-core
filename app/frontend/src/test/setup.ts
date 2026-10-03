import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrolling
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => undefined;
