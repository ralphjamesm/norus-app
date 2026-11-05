$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path $envPath)) {
	throw "No se encontró .env. Ejecuta primero .\scripts\configure-local.ps1."
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
	$password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
	if ($password.Contains("`r") -or $password.Contains("`n")) {
		throw "La contraseña de MySQL no puede contener saltos de línea."
	}
	$escapedPassword = $password.Replace("\", "\\").Replace('"', '\"')
	$config = "[client]`r`nuser=$adminUser`r`npassword=`"$escapedPassword`"`r`nhost=127.0.0.1`r`nport=3306`r`n"
	$encoding = [System.Text.UTF8Encoding]::new($false)
	[System.IO.File]::WriteAllText($temporaryConfig, $config, $encoding)
	$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
	& icacls.exe $temporaryConfig /inheritance:r /grant:r "${identity}:(M)" | Out-Null
	if ($LASTEXITCODE -ne 0) {
		throw "No se pudieron restringir los permisos del archivo temporal."
	}

	$sql = @"
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER, REFERENCES ON norus.* TO 'norus_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER, REFERENCES ON norus.* TO 'norus_app'@'127.0.0.1';
FLUSH PRIVILEGES;
"@
	$sql | & $mysql "--defaults-extra-file=$temporaryConfig" --batch --skip-column-names
	if ($LASTEXITCODE -ne 0) {
		throw "MySQL rechazó la reparación. Comprueba usuario, contraseña y que exista la base norus."
	}

	$envContent = [System.IO.File]::ReadAllText($envPath)
	if ($envContent -notmatch '(?m)^NORUS_BOOTSTRAP_ADMIN_PASSWORD=') {
		throw "No se encontró NORUS_BOOTSTRAP_ADMIN_PASSWORD en .env; revisa el archivo sin compartir su contenido."
	}
	$randomBytes = New-Object byte[] 32
	$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
	try {
		$generator.GetBytes($randomBytes)
	} finally {
		$generator.Dispose()
	}
	$newAdminPassword = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
	$envContent = [System.Text.RegularExpressions.Regex]::Replace(
		$envContent,
		'(?m)^NORUS_BOOTSTRAP_ADMIN_PASSWORD=.*$',
		"NORUS_BOOTSTRAP_ADMIN_PASSWORD=$newAdminPassword"
	)
	$encoding = [System.Text.UTF8Encoding]::new($false)
	[System.IO.File]::WriteAllText($envPath, $envContent, $encoding)
	& icacls.exe $envPath /inheritance:r /grant:r "${identity}:(M)" | Out-Null
	if ($LASTEXITCODE -ne 0) {
		throw "Se rotó la contraseña, pero no se pudieron restringir los permisos de .env."
	}
	"Permisos de REFERENCES añadidos a norus_app. No se modificaron ni borraron datos."
	"Se generó una nueva contraseña temporal de administrador. Guárdala ahora: $newAdminPassword"
} finally {
	if ($passwordPointer -ne [IntPtr]::Zero) {
		[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
	}
	if (Test-Path $temporaryConfig) {
		Remove-Item -LiteralPath $temporaryConfig -Force
	}
	$password = $null
	$escapedPassword = $null
	$securePassword = $null
	$envContent = $null
}
