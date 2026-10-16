"""Ayarlar sekmesinin gruplarını yeniden kullanılabilir `QGroupBox`'lara ayırır (PLAN.md 10.2c).

Her grup kendi widget'larını kurar, `values()` ile kendi `SettingsForm` alanlarını sözlük olarak
verir, `set_values(form)` ile ekranı doldurur. Ağa çıkan "test et" işleri ve sekmeler arası bağlantı
grubun İÇİNDE değil, onu barındıran pencerede (`SettingsTab`, ileride sihirbaz) yapılır: grup
yalnızca bir sinyal yayar.
"""
