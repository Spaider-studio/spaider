import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only cleans up after itself when the test runner exposes
// globals, which this project does not. Without this the DOM of one test leaks
// into the next.
afterEach(cleanup);
