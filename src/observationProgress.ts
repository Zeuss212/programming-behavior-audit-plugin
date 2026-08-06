import { IBehaviorSegment } from './behaviorSegments';

export interface IObservationProgress {
  validObservationDurationMs: number;
  pageAwayDurationMs: number;
  observationAnchorAt: string | null;
}

type ObservationSegment = Pick<
  IBehaviorSegment,
  'segment_type' | 'started_at' | 'ended_at'
> &
  Partial<Pick<IBehaviorSegment, 'duration_ms'>>;

type Interval = readonly [startMs: number, endMs: number];

const VALID_OBSERVATION_TYPES = new Set<IBehaviorSegment['segment_type']>([
  'code_writing',
  'code_deletion',
  'code_paste',
  'idle'
]);

const EXCLUDED_OBSERVATION_TYPES = new Set<IBehaviorSegment['segment_type']>([
  'page_away',
  'code_execution'
]);

export function calculateObservationProgress(
  segments: ReadonlyArray<ObservationSegment>
): IObservationProgress {
  const validIntervals: Interval[] = [];
  const excludedIntervals: Interval[] = [];
  const pageAwayIntervals: Interval[] = [];
  let observationAnchorAt: string | null = null;
  let observationAnchorMs = Number.NEGATIVE_INFINITY;

  for (const segment of segments) {
    const interval = parseInterval(segment.started_at, segment.ended_at);
    if (!interval) {
      continue;
    }

    if (interval[1] > observationAnchorMs) {
      observationAnchorMs = interval[1];
      observationAnchorAt = segment.ended_at;
    }

    const hasDuration = interval[1] > interval[0];
    if (hasDuration && VALID_OBSERVATION_TYPES.has(segment.segment_type)) {
      validIntervals.push(interval);
    }
    if (hasDuration && EXCLUDED_OBSERVATION_TYPES.has(segment.segment_type)) {
      excludedIntervals.push(interval);
    }
    if (hasDuration && segment.segment_type === 'page_away') {
      pageAwayIntervals.push(interval);
    }
  }

  const mergedValid = mergeIntervals(validIntervals);
  const mergedExcluded = mergeIntervals(excludedIntervals);
  const validDurationMs = sumDuration(mergedValid);
  const excludedOverlapMs = intersectionDuration(mergedValid, mergedExcluded);

  return {
    validObservationDurationMs: Math.max(
      0,
      validDurationMs - excludedOverlapMs
    ),
    pageAwayDurationMs: sumDuration(mergeIntervals(pageAwayIntervals)),
    observationAnchorAt
  };
}

function parseInterval(startedAt: string, endedAt: string): Interval | null {
  const startMs = Date.parse(startedAt);
  const endMs = Date.parse(endedAt);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return null;
  }
  return [startMs, endMs];
}

function mergeIntervals(intervals: ReadonlyArray<Interval>): Interval[] {
  const ordered = [...intervals].sort(
    ([leftStart], [rightStart]) => leftStart - rightStart
  );
  const merged: Interval[] = [];

  for (const interval of ordered) {
    const previous = merged[merged.length - 1];
    if (!previous || interval[0] > previous[1]) {
      merged.push(interval);
      continue;
    }
    merged[merged.length - 1] = [
      previous[0],
      Math.max(previous[1], interval[1])
    ];
  }

  return merged;
}

function sumDuration(intervals: ReadonlyArray<Interval>): number {
  return intervals.reduce((total, [startMs, endMs]) => {
    return total + (endMs - startMs);
  }, 0);
}

function intersectionDuration(
  leftIntervals: ReadonlyArray<Interval>,
  rightIntervals: ReadonlyArray<Interval>
): number {
  let leftIndex = 0;
  let rightIndex = 0;
  let durationMs = 0;

  while (
    leftIndex < leftIntervals.length &&
    rightIndex < rightIntervals.length
  ) {
    const left = leftIntervals[leftIndex];
    const right = rightIntervals[rightIndex];
    durationMs += Math.max(
      0,
      Math.min(left[1], right[1]) - Math.max(left[0], right[0])
    );

    if (left[1] <= right[1]) {
      leftIndex += 1;
    } else {
      rightIndex += 1;
    }
  }

  return durationMs;
}
