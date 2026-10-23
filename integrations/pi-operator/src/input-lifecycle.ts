export type ObserverInputState = "open" | "eof" | "errored";

/** Only a real stdin EOF is a normal non-interactive observer shutdown. */
export function shouldExitAfterInputFailure(
  stopping: boolean,
  inputState: ObserverInputState,
  readableEnded: boolean,
): boolean {
  return stopping || inputState === "eof" || readableEnded;
}
