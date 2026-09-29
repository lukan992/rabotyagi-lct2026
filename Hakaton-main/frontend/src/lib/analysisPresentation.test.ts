import { describe, expect, it } from 'vitest'
import type { ServiceAnswer, SpiderStageLinks, WorkGroup } from '@/data'
import { analysisErrorMessage, manualStageCodes, workGroupPrefix } from './analysisPresentation'

const group = { match: 'specific', works: [{ stepKey: 'local-work' }] } as WorkGroup
const links: SpiderStageLinks = {
  snapshotId: 'snapshot-1',
  stages: [{ stageCode: 'P04', stageName: 'Разработка', stepKey: 'local-work', stepName: 'Котлован' }],
  localSteps: [{ stepKey: 'local-work', name: 'Котлован' }],
}

describe('presentation of photo analysis', () => {
  it('shows a manual stage code only for the Spider snapshot used by this analysis', () => {
    const answer = { spiderSnapshotId: 'snapshot-1' } as ServiceAnswer
    expect(manualStageCodes(answer, group, links)).toEqual(['P04'])
    expect(manualStageCodes({ ...answer, spiderSnapshotId: 'snapshot-2' }, group, links)).toEqual([])
    expect(manualStageCodes({ ...answer, spiderSnapshotId: null }, group, links)).toEqual([])
  })

  it('labels VLM work as a hypothesis and keeps technical failures out of main status', () => {
    expect(workGroupPrefix({ service: 'vlm_llm' } as ServiceAnswer, group)).toBe('Предположение по фото: ')
    expect(workGroupPrefix({ service: 'vlm_llm' } as ServiceAnswer, { ...group, match: 'ambiguous' })).toBe('Возможные работы: ')
    expect(analysisErrorMessage({ errorCode: 'model_invalid_response' } as ServiceAnswer)).not.toContain('HTTP')
  })
})
