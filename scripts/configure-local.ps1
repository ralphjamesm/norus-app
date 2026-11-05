param(
	[string]$AdminEmail = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function New-Base64UrlSecret([int]$length) {
	$bytes = New-Object byte[] $length
	$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
	try {
		$generator.GetBytes($bytes)
	} finally {
		$generator.Dispose()
	}
	return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function New-HexSecret([int]$length) {
	$bytes = New-Object byte[] $length
	$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
	try {
		$generator.GetBytes($bytes)
	} finally {
		$generator.Dispose()
	}
	return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Set-PrivateFileAcl([string]$Path) {
	$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
	& icacls.exe $Path /inheritance:r /grant:r "${identity}:(M)" | Out-Null
	if ($LASTEXITCODE -ne 0) {
		throw "No se pudieron restringir los permisos del archivo $Path."
	}
}

$AdminEmail = if ([string]::IsNullOrWhiteSpace($AdminEmail)) {
	Read-Host "Correo para la primera cuenta administradora"
} else {
	$AdminEmail
}
if ($AdminEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
	throw "Indica un correo válido para la cuenta administradora."
}
if (Test-Path ".env") {
	throw "Ya existe .env. Para evitar sobrescribir secretos, no se realizaron cambios."
}

$mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
if (-not (Test-Path $mysql)) {
	throw "No se encontró el cliente MySQL en la ubicación esperada: $mysql"
}
$service = Get-Service -Name "MySQL80" -ErrorAction SilentlyContinue
if (-not $service -or $service.Status -ne "Running") {
	throw "El servicio MySQL80 no está iniciado."
}

$adminUser = Read-Host "Usuario administrador local de MySQL [root]"
if ([string]::IsNullOrWhiteSpace($adminUser)) {
	$adminUser = "root"
}
if ($adminUser -notmatch '^[A-Za-z0-9_.$-]+$') {
	throw "El nombre de usuario MySQL contiene caracteres no admitidos."
}
$securePassword = Read-Host "Contraseña administrador MySQL (no se mostrará)" -AsSecureString
$passwordPointer = [IntPtr]::Zero
$temporaryConfig = Join-Path $env:TEMP ("norus-mysql-" + [Guid]::NewGuid().ToString("N") + ".cnf")

try {
	$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
	$mysqlPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
	if ($mysqlPassword.Contains("`r") -or $mysqlPassword.Contains("`n")) {
		throw "La contraseña de MySQL no puede contener saltos de línea."
	}
	$escapedPassword = $mysqlPassword.Replace("\", "\\").Replace('"', '\"')
	$configContent = "[client]`r`nuser=$adminUser`r`npassword=`"$escapedPassword`"`r`nhost=127.0.0.1`r`nport=3306`r`n"
	[System.IO.File]::WriteAllText($temporaryConfig, $configContent, $utf8NoBom)
	Set-PrivateFileAcl $temporaryConfig

	$existingDatabase = @("" | & $mysql "--defaults-extra-file=$temporaryConfig" --batch --skip-column-names --execute "SHOW DATABASES LIKE 'norus';")
	if ($LASTEXITCODE -ne 0) {
		throw "No se pudo consultar MySQL. Revisa el usuario y la contraseña ingresados."
	}
	if (($existingDatabase -join "").Trim() -eq "norus") {
		$confirmation = Read-Host "La base norus ya existe. No se borrarán datos, pero se actualizará la contraseña de norus_app. ¿Continuar? [s/N]"
		if ($confirmation -notmatch '^(s|si|sí)$') {
			throw "Configuración cancelada; la base de datos no fue modificada."
		}
	}

	$databasePassword = New-HexSecret 32
	$sql = @"
CREATE DATABASE IF NOT EXISTS norus CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'norus_app'@'localhost' IDENTIFIED BY '$databasePassword';
ALTER USER 'norus_app'@'localhost' IDENTIFIED BY '$databasePassword';
CREATE USER IF NOT EXISTS 'norus_app'@'127.0.0.1' IDENTIFIED BY '$databasePassword';
ALTER USER 'norus_app'@'127.0.0.1' IDENTIFIED BY '$databasePassword';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER, REFERENCES ON norus.* TO 'norus_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER, REFERENCES ON norus.* TO 'norus_app'@'127.0.0.1';
FLUSH PRIVILEGES;
"@
	$sql | & $mysql "--defaults-extra-file=$temporaryConfig" --batch --skip-column-names
	if ($LASTEXITCODE -ne 0) {
		throw "MySQL rechazó la configuración. Revisa el usuario y la contraseña ingresados."
	}

	$adminPassword = New-Base64UrlSecret 32
	$sessionKey = New-Base64UrlSecret 48
	$documentKey = New-Base64UrlSecret 32
	$envContent = @"
DATABASE_URL=mysql+pymysql://norus_app:$databasePassword@127.0.0.1:3306/norus?charset=utf8mb4
NORUS_SECRET_KEY=$sessionKey
NORUS_DOCUMENT_KEY=$documentKey
NORUS_COOKIE_SECURE=false
NORUS_BOOTSTRAP_ADMIN_EMAIL=$($AdminEmail.Trim().ToLowerInvariant())
NORUS_BOOTSTRAP_ADMIN_PASSWORD=$adminPassword
PUBLIC_APP_URL=http://127.0.0.1:8000
HOST=127.0.0.1
PORT=8000
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=noreply@example.com
SMTP_STARTTLS=true
PROPERTY_EMAIL_LAS_CONDES=
PROPERTY_EMAIL_PROVIDENCIA=
PROPERTY_EMAIL_LUUM=
"@
	$envPath = Join-Path $projectRoot ".env"
	[System.IO.File]::WriteAllText($envPath, $envContent, $utf8NoBom)
	Set-PrivateFileAcl $envPath

	"`nConfiguración local MySQL creada."
	"Cuenta administradora: $($AdminEmail.Trim().ToLowerInvariant())"
	"Contraseña temporal del administrador (guárdala; no se volverá a mostrar): $adminPassword"
	"SMTP queda pendiente de las credenciales del proveedor y los correos de cada sede."
	"Ahora inicia la app con: .\.venv\Scripts\python.exe backend\run.py"
} finally {
	if ($passwordPointer -ne [IntPtr]::Zero) {
		[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
	}
	if (Test-Path $temporaryConfig) {
		Remove-Item -LiteralPath $temporaryConfig -Force
	}
	$mysqlPassword = $null
	$escapedPassword = $null
	$securePassword = $null
}
