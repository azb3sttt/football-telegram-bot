from telegram import InlineKeyboardButton, InlineKeyboardMarkup


NOTIFICATION_LABELS = {
    "coupon_result": "Wynik kuponu",
    "selection_result": "Typ zakończony",
    "daily_report": "Raport dzienny",
    "weekly_report": "Raport tygodniowy",
    "monthly_report": "Raport miesięczny",
}


def notifications_keyboard(settings) -> InlineKeyboardMarkup:
    rows = []
    for field, label in NOTIFICATION_LABELS.items():
        enabled = bool(getattr(settings, field))
        rows.append(
            [InlineKeyboardButton(f"{'✅' if enabled else '❌'} {label}", callback_data=f"notif:{field}")]
        )
    return InlineKeyboardMarkup(rows)
