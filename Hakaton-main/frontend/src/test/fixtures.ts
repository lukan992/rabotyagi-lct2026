import type { Alert, Camera, Snapshot, Stage } from '@/data'

/** Заготовки данных для тестов: указываются только поля, важные для проверки */

export function stage(p: Partial<Stage> & Pick<Stage, 'start' | 'end'>): Stage {
  return {
    id: 'st', siteId: 's1', parentId: null, level: 2, name: 'Работа', status: 'in_progress', ruleKey: null,
    planProgress: 0, factProgress: 0, factUpdatedAt: null, catalogStageId: null, catalogVersion: null, ...p,
  }
}

export function camera(p: Partial<Camera> = {}): Camera {
  return {
    id: 'c1', siteId: 's1', zoneId: 'z1', name: 'Камера 1', online: true, enabled: true, spiderEnabled: false, status: 'online', sourceType: 'rtsp',
    address: null, hasCredentials: false, demo: false, streamPath: 'cam-c1', lastError: null, lastSnapshotAt: null, scene: 'pit', ...p,
  }
}

export function snapshot(detections: Snapshot['detections']): Snapshot {
  return { id: `sn-${detections.length}`, cameraId: 'c1', takenAt: '2026-09-25T10:00:00Z', imageUrl: '', detections, analyzed: true, provider: null, note: null }
}

export function alert(p: Partial<Alert> = {}): Alert {
  return {
    id: 'a1', code: 'ОТК-26-0001', siteId: 's1', zoneId: 'z1', stageId: null, cameraId: 'c1', kind: 'missing', severity: 'high',
    status: 'new', isOpen: true, title: 'Нет самосвалов', summary: '', consequence: '',
    advice: 'Выяснить у подрядчика, где самосвалы и когда они будут на площадке.', equipment: 'dump_truck', expected: 2, observed: 0,
    prescriptionNo: null, prescriptionDue: null, startedAt: '2026-09-25T10:00:00Z', updatedAt: '2026-09-25T10:00:00Z',
    evidence: [], evidenceSnapshots: [], history: [], ...p,
  } as Alert
}

/** Дата относительно «сегодня» в тестах: day(-4) — четыре дня назад */
export const TODAY = '2026-09-10'
export function day(offset: number) {
  const d = new Date(`${TODAY}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + offset)
  return d.toISOString().slice(0, 10)
}
