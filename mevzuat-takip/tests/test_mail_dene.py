import argparse
import smtplib

import pytest

from mevzuat import cli


def test_smtp_yoksa_dosyaya_yazar(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEVZUAT_SMTP_HOST", raising=False)
    cli.mail_dene_komutu(argparse.Namespace(adres="deneme@firma.test"))
    assert "giden_mailler" in capsys.readouterr().out
    eml = list((tmp_path / "giden_mailler").glob("*.eml"))
    assert len(eml) == 1 and b"deneme@firma.test" in eml[0].read_bytes()


def test_yanlis_sifre_anlasilir_mesaj(monkeypatch):
    class Reddeden:
        def gonder(self, mail):
            raise smtplib.SMTPAuthenticationError(535, b"BadCredentials")

    monkeypatch.setattr(cli, "_gonderici", lambda ayarlar: Reddeden())
    with pytest.raises(SystemExit, match="Uygulama şifreleri"):
        cli.mail_dene_komutu(argparse.Namespace(adres="deneme@firma.test"))


def test_baglanti_hatasi(monkeypatch):
    class Ulasilamayan:
        def gonder(self, mail):
            raise OSError("bağlantı reddedildi")

    monkeypatch.setattr(cli, "_gonderici", lambda ayarlar: Ulasilamayan())
    with pytest.raises(SystemExit, match="gönderilemedi"):
        cli.mail_dene_komutu(argparse.Namespace(adres="deneme@firma.test"))
