"""Certificat HTTPS interne (auto-signé) pour le réseau local : python make_cert.py <nom-DNS> [adresse-IP ...]

Produit server.crt et server.key dans le dossier courant. Importer server.crt dans les « Autorités de
certification racines de confiance » des postes clients (GPO ou manuellement) pour supprimer l'avertissement.
"""
import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    name, ips = argv[0], argv[1:]
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    san = [x509.DNSName(name), x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    Path("server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    Path("server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print("server.crt et server.key créés (valables 825 jours). Protégez server.key (droits restreints).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
