import { PageHeader } from '@/components/ui/PageHeader'
import { CAMERA_ADDERS, EQUIPMENT_LIST, SITE_MANAGERS } from '@/data'
import { useApp } from '@/store/context'
import { VehicleIcon } from '@/components/VehicleIcon'

const STEPS = [
  ['Камеры показывают видео', 'Видео со всех камер стройки идёт в систему постоянно. Его можно смотреть в разделе «Камеры» — нажмите на камеру, и она развернётся на всю вкладку.'],
  ['Система находит технику', 'Каждые 2 секунды она берёт кадр из видео и находит на нём экскаваторы, самосвалы, краны и другую технику.'],
  ['Сверяет с планом работ', 'Раз в минуту смотрит, какой этап идёт по календарному плану и какая техника для него нужна.'],
  ['Сообщает, если что-то не так', 'Нет нужной техники, приехала лишняя, машина стоит без дела — вы получите сообщение с кадром-доказательством.'],
]

/** Справка: обычный текст, без украшений */
export function Help() {
  const { role } = useApp()
  return (
    <div className="max-w-3xl">
      <PageHeader title="Справка" />

      <Section title="Как это работает">
        <ol className="list-decimal pl-6 space-y-2">
          {STEPS.map(([t, d]) => <li key={t}><b>{t}.</b> {d}</li>)}
        </ol>
      </Section>

      <Section title="Что означают цвета">
        <table className="w-full text-[15px]">
          <tbody className="divide-y divide-border">
            <tr><td className="py-2 pr-3 w-32"><span className="inline-block w-2.5 h-2.5 rounded-full bg-ok mr-2" />Зелёный</td><td className="py-2">Нет открытых замечаний в журнале. Это не подтверждает соблюдение графика.</td></tr>
            <tr><td className="py-2 pr-3"><span className="inline-block w-2.5 h-2.5 rounded-full bg-warn mr-2" />Жёлтый</td><td className="py-2">Есть замечания. Посмотрите, когда будет время.</td></tr>
            <tr><td className="py-2 pr-3"><span className="inline-block w-2.5 h-2.5 rounded-full bg-danger mr-2" />Красный</td><td className="py-2">Нужно вмешаться сейчас: работы стоят или под угрозой качество.</td></tr>
          </tbody>
        </table>
      </Section>

      <Section title="Что делать, если пришло сообщение">
        <ol className="list-decimal pl-6 space-y-2">
          <li>Откройте сообщение и посмотрите кадр — на нём обведено, что увидела система. Кнопка «Смотреть камеру сейчас» покажет, что там происходит прямо сейчас.</li>
          <li>Прочитайте раздел «Что делать» — там короткая подсказка.</li>
          <li>Нажмите одну из кнопок: <b>«Техника едет»</b>, <b>«Подтверждаю проблему»</b> или <b>«Это ошибка»</b>. Комментарий писать необязательно.</li>
          <li>Когда проблема решена — нажмите <b>«Устранено»</b>.</li>
        </ol>
      </Section>

      <Section title="Как читать план работ">
        <ul className="list-disc pl-6 space-y-2">
          <li>План разделён на <b>завершённые</b>, <b>текущие</b> и <b>будущие</b> этапы.</li>
          <li>У текущих этапов заливка полосы — сколько сделано <b>по факту</b>, а тёмная черта — где работы должны быть <b>по графику</b> на сегодня. Заливка левее черты — отставание, система пишет его и в днях.</li>
          <li>Отставание в днях: то, что сделано сейчас, по графику должно было быть готово столько дней назад. Так же оно считается у всего объекта. Один день — в пределах точности отметок, от пяти дней — красным.</li>
          <li>Сколько сделано, отмечают руководитель проекта и администратор: кнопка <b>«Отметить выполнение»</b> у работы.</li>
        </ul>
      </Section>

      {role && SITE_MANAGERS.includes(role.id) && (
        <Section title="Как добавить объект">
          <ol className="list-decimal pl-6 space-y-2">
            <li>Раздел «Объекты» → <b>«Добавить объект»</b>: название, адрес, подрядчик, прораб.</li>
            <li>Откроется страница нового объекта. Вкладка «План работ» → <b>«Изменить план»</b>: этапы и работы с правилами «этап → техника».</li>
            <li>Вкладка «Камеры» → <b>«Камера на этот объект»</b>. Зоны объекта — кнопка «Зоны» рядом с названием.</li>
          </ol>
        </Section>
      )}

      {role && CAMERA_ADDERS.includes(role.id) && (
        <Section title="Как подключить новую камеру">
          <ol className="list-decimal pl-6 space-y-2">
            <li>Раздел «Камеры» → <b>«Добавить камеру»</b> (или «Камера на этот объект» у нужного объекта).</li>
            <li>Выберите объект, зону, за которой камера следит, и производителя — путь к видео подставится сам.</li>
            <li>Введите IP-адрес камеры, логин и пароль и нажмите <b>«Проверить и показать видео»</b>: если всё верно, в форме появится живое видео.</li>
            <li>Нажмите <b>«Добавить камеру»</b>. Через несколько секунд она появится среди камер, и система начнёт разбирать её видео.</li>
          </ol>
        </Section>
      )}

      <Section title="Какую технику система узнаёт">
        <ul className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2">
          {EQUIPMENT_LIST.map((e) => (
            <li key={e.type} className="flex items-center gap-2">
              <VehicleIcon type={e.type} className="w-10 h-6 shrink-0" fill={e.color} /> {e.name}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-card border border-border rounded-xl shadow-[var(--shadow-card)] mb-4 p-5 sm:p-6">
      <h2 className="text-[18px] font-semibold mb-3">{title}</h2>
      <div className="leading-relaxed">{children}</div>
    </section>
  )
}
