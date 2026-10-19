/**
 * The watch contract other extensions build on. Extensions can't share live
 * module state (pi loads each one with its own module cache), so this file
 * only holds types and channel names; everything live travels over
 * `pi.events`.
 */

/** Emitted with a {@link WatchActiveEvent} whenever the number of active watches changes. */
export const WATCH_ACTIVE_CHANNEL = "watch:active";

export interface WatchActiveEvent {
  count: number;
}
