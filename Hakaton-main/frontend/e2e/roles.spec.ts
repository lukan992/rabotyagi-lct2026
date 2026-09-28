import { expect, test, type Page } from '@playwright/test'

/**
 * Смоук по ролям: вход с паролем, все экраны роли открываются без ошибок.
 * Тестовый сервер отдельно наполняет временную базу для проверки сценариев.
 */

/** Ошибки страницы: необработанные исключения и сообщения об ошибках в консоли */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(`исключение: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`консоль: ${message.text()}`)
  })
  return errors
}

async function enterAs(page: Page, role: 'Прораб' | 'Руководитель проекта' | 'Инспектор' | 'Администратор') {
  const login = { Прораб: 'prorab', 'Руководитель проекта': 'rukovoditel', Инспектор: 'inspektor', Администратор: 'admin' }[role]
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Логин' }).fill(login)
  await page.getByLabel('Пароль', { exact: true }).fill('e2e-password')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Вход в систему' })).toHaveCount(0)
}

/** Открыть экран по адресу и дождаться его содержимого; экрана «Что-то пошло не так» быть не должно */
async function visit(page: Page, path: string, marker: string | RegExp) {
  await page.goto(path)
  await expect(page.getByText(marker).first()).toBeVisible()
  await expect(page.getByText('Что-то пошло не так')).toHaveCount(0)
}

test('прораб: свой объект, камеры, план; отклонение открывается и закрывается @телефон', async ({ page }) => {
  const errors = watchErrors(page)
  await enterAs(page, 'Прораб')
  await visit(page, '/foreman', 'Что не так прямо сейчас')
  await page.getByRole('button', { name: /Нет самосвалов/ }).first().click()
  const detail = page.getByRole('dialog')
  await expect(detail).toBeVisible()
  await expect(detail.getByText('Почему система так решила')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(detail).toHaveCount(0)
  await visit(page, '/foreman/cameras', 'Камера 1 — котлован')
  await visit(page, '/foreman/plan', 'Разработка котлована')
  // выполнение прораб видит, но не отмечает — это делает руководитель
  await expect(page.getByText(/Сделано по факту|По факту/).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /Отметить выполнение/ })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('руководитель: объекты, страница объекта с вкладками, план из CSV, камеры, отклонения, отчёт, проверка фото', async ({ page }) => {
  const errors = watchErrors(page)
  await enterAs(page, 'Руководитель проекта')
  await visit(page, '/manager', 'ЖК «Северный парк», корпус 3')
  await visit(page, '/manager/site/s1', 'Этап сейчас')
  // пояснение «?» открывается нажатием и закрывается Escape; текст зачитывает и экранный диктор
  const tip = page.getByRole('button', { name: 'Как считается техника' })
  await tip.click()
  await expect(tip).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('status').filter({ hasText: 'Сколько техники нужно по правилу этапа' })).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(tip).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('status').filter({ hasText: 'Сколько техники нужно по правилу этапа' })).toHaveCount(0)
  // работы по камерам: сервисы аналитики (имитация) подключены, свежих кадров без видео нет — отправлять нечего
  await expect(page.getByRole('heading', { name: 'Работы по камерам' })).toBeVisible()
  await expect(page.getByText('Кадр этой камеры ещё не отправлялся')).toBeVisible()
  await page.getByRole('tab', { name: 'План работ' }).click()
  await expect(page.getByRole('tab', { name: 'План работ' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('Разработка котлована').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /Отметить выполнение/ }).first()).toBeVisible()  // руководитель отмечает
  // у каждой работы плана — вид работ по справочнику сервисов, выбранный человеком
  await page.getByRole('button', { name: 'Изменить план' }).click()
  const plan = page.getByRole('dialog')
  await expect(plan.getByText('По справочнику: «Выемка грунта котлована»').first()).toBeVisible()
  await plan.getByRole('listitem').filter({ hasText: 'Разработка котлована' }).getByRole('button', { name: 'Изменить' }).click()
  const kind = plan.getByRole('combobox', { name: 'Вид работ по справочнику' })
  await expect(kind).toBeVisible()
  await expect(kind.locator('option', { hasText: 'Обратная засыпка грунтом' })).toHaveCount(1)
  await page.keyboard.press('Escape')
  // план из Excel или CSV: предпросмотр с ошибками по строкам; загрузить можно, только когда ошибок нет
  await page.getByRole('button', { name: 'Загрузить из Excel' }).click()
  const upload = page.getByRole('dialog', { name: /План из Excel/ })
  const csv = (end: string) => ({
    name: 'план.csv', mimeType: 'text/csv', buffer: Buffer.from(`Этап;Работа;Начало;Окончание\nОтделка;Внутренняя отделка;01.06.2027;${end}\n`),
  })
  await upload.locator('input[type=file]').setInputFiles(csv('01.05.2027'))
  await expect(upload.getByText('Окончание раньше начала')).toBeVisible()
  await expect(upload.getByRole('button', { name: 'Загрузить план' })).toBeDisabled()
  await upload.locator('input[type=file]').setInputFiles(csv('31.08.2027'))
  await upload.getByRole('button', { name: 'Загрузить 1 работу' }).click()
  await expect(upload).toHaveCount(0)
  await expect(page.getByText('План загружен: 1 этап, 1 работа')).toBeVisible()
  await visit(page, '/manager/cameras', 'Камера 2 — въезд')
  await visit(page, '/manager/alerts', 'Нет самосвалов')
  await visit(page, '/manager/reports', 'Отчёт за неделю')
  await visit(page, '/manager/check', 'Проверить фото')
  expect(errors).toEqual([])
})

test('инспектор: журнал нарушений, форма предписания, отчёт', async ({ page }) => {
  const errors = watchErrors(page)
  await enterAs(page, 'Инспектор')
  await visit(page, '/inspector', 'Журнал нарушений')
  await page.getByRole('button', { name: /^Открыть нарушение/ }).first().click()
  await page.getByRole('button', { name: 'Выдать предписание…' }).first().click()
  const form = page.getByRole('dialog', { name: 'Выдать предписание' })
  await expect(form).toBeVisible()
  await expect(form.getByText('Срок устранения')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(form).toHaveCount(0)
  await visit(page, '/inspector/cameras', 'Камера 1 — котлован')
  await visit(page, '/inspector/reports', 'Отчёт за неделю')
  expect(errors).toEqual([])
})

test('администратор: сотрудники с меню действий, правила (добавить и удалить), журнал действий, объекты', async ({ page }) => {
  const errors = watchErrors(page)
  await enterAs(page, 'Администратор')
  await visit(page, '/admin', 'ЖК «Северный парк», корпус 3')
  await visit(page, '/admin/manage/users', 'Сотрудники')
  // редкие действия — в меню «⋯»: открывается, фокус на первом пункте, Escape закрывает
  await page.getByRole('button', { name: /^Ещё действия:/ }).first().click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: 'Сменить пароль' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  // камеры: изменить, проверить связь, удалить — а выключить нельзя никому
  await visit(page, '/admin/cameras', 'Камера 1 — котлован')
  await expect(page.getByRole('button', { name: /Изменить/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /^Выключить|^Включить/ })).toHaveCount(0)
  await visit(page, '/admin/manage/rules', 'Правила: этап → техника')
  // новое правило: название → сразу открывается его редактор; удалить можно, пока его не выбрали у работ плана
  await page.getByRole('button', { name: 'Добавить правило' }).click()
  const form = page.getByRole('dialog', { name: 'Новое правило' })
  await form.getByLabel('Название этапа').fill('Разработка котлована')
  await form.getByRole('button', { name: 'Добавить правило' }).click()
  await expect(form.getByText('Правило «Разработка котлована» уже есть')).toBeVisible()
  await form.getByLabel('Название этапа').fill('Монтаж наружных сетей')
  await form.getByRole('button', { name: 'Добавить правило' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Название этапа' })).toHaveValue('Монтаж наружных сетей')
  await page.getByRole('button', { name: 'Удалить правило' }).click()
  const confirm = page.getByRole('dialog', { name: 'Удалить правило?' })
  await confirm.getByRole('button', { name: 'Удалить правило' }).click()
  await expect(confirm).toHaveCount(0)
  await expect(page.getByText('Монтаж наружных сетей', { exact: true })).toHaveCount(0)
  await visit(page, '/admin/manage/audit', 'Журнал действий')
  await visit(page, '/admin/site/s3', 'Этап сейчас')
  expect(errors).toEqual([])
})

