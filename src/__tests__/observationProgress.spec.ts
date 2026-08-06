import { IBehaviorSegment, BehaviorSegmentType } from '../behaviorSegments';
import { calculateObservationProgress } from '../observationProgress';

const ORIGIN_MS = Date.parse('2026-07-30T08:00:00.000Z');

function iso(offsetMs: number): string {
  return new Date(ORIGIN_MS + offsetMs).toISOString();
}

function segment(
  segmentType: BehaviorSegmentType,
  startedAtMs: number,
  endedAtMs: number
): IBehaviorSegment {
  return {
    segment_type: segmentType,
    started_at: iso(startedAtMs),
    ended_at: iso(endedAtMs),
    duration_ms: endedAtMs - startedAtMs,
    document_type: 'notebook_cell',
    notebook_path: 'synthetic.ipynb',
    cell_id: 'cell-synthetic',
    cell_index: 0
  };
}

describe('calculateObservationProgress', () => {
  it('merges valid intervals and subtracts execution and page-away overlaps', () => {
    const progress = calculateObservationProgress([
      segment('code_writing', 0, 10_000),
      segment('idle', 8_000, 20_000),
      segment('code_execution', 9_000, 11_000),
      segment('page_away', 15_000, 18_000)
    ]);

    expect(progress).toEqual({
      validObservationDurationMs: 15_000,
      pageAwayDurationMs: 3_000,
      observationAnchorAt: iso(20_000)
    });
  });

  it('derives duration from timestamps instead of duration_ms', () => {
    const progress = calculateObservationProgress([
      {
        ...segment('idle', 0, 5_000),
        duration_ms: 999_999
      }
    ]);

    expect(progress.validObservationDurationMs).toBe(5_000);
  });

  it('uses a zero-length paste as the anchor without adding duration', () => {
    const progress = calculateObservationProgress([
      segment('code_paste', 8_000, 8_000)
    ]);

    expect(progress).toEqual({
      validObservationDurationMs: 0,
      pageAwayDurationMs: 0,
      observationAnchorAt: iso(8_000)
    });
  });

  it('ignores invalid and reversed timestamps', () => {
    const progress = calculateObservationProgress([
      {
        ...segment('idle', 0, 5_000),
        started_at: 'invalid'
      },
      segment('code_writing', 7_000, 6_000)
    ]);

    expect(progress).toEqual({
      validObservationDurationMs: 0,
      pageAwayDurationMs: 0,
      observationAnchorAt: null
    });
  });
});
