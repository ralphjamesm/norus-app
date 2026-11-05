CREATE DATABASE IF NOT EXISTS norus
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
USE norus;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS incidencia;
DROP TABLE IF EXISTS solicitud_servicio;
DROP TABLE IF EXISTS pre_checkin;
DROP TABLE IF EXISTS reserva;
DROP TABLE IF EXISTS estado_reserva;
DROP TABLE IF EXISTS cliente;
DROP TABLE IF EXISTS historia_habitacion;
DROP TABLE IF EXISTS departamento;
DROP TABLE IF EXISTS sede;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE sede (
  id_sede INT NOT NULL AUTO_INCREMENT,
  nombre_sede VARCHAR(100) NOT NULL,
  direccion VARCHAR(150) NOT NULL,
  email_contacto VARCHAR(100) NOT NULL,
  PRIMARY KEY (id_sede)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE departamento (
  id_depto INT NOT NULL AUTO_INCREMENT,
  id_sede INT NOT NULL,
  numero_departamento VARCHAR(20) NOT NULL,
  codigo_cerradura VARCHAR(100) NOT NULL,
  clave_wifi VARCHAR(100) NULL,
  estado_actual VARCHAR(30) NOT NULL DEFAULT 'disponible',
  PRIMARY KEY (id_depto),
  UNIQUE KEY uq_departamento_sede_numero (id_sede, numero_departamento),
  KEY idx_departamento_sede (id_sede),
  CONSTRAINT fk_departamento_sede
    FOREIGN KEY (id_sede) REFERENCES sede (id_sede)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE cliente (
  id_cliente INT NOT NULL AUTO_INCREMENT,
  rut_empresa VARCHAR(20) NULL,
  razon_social VARCHAR(150) NOT NULL,
  nombre_fantasia VARCHAR(150) NULL,
  email_facturacion VARCHAR(100) NOT NULL,
  telefono_contacto VARCHAR(30) NULL,
  fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activo TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id_cliente)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE estado_reserva (
  id_estado INT NOT NULL AUTO_INCREMENT,
  codigo_estado VARCHAR(30) NOT NULL,
  descripcion VARCHAR(120) NOT NULL,
  PRIMARY KEY (id_estado),
  UNIQUE KEY uq_estado_codigo (codigo_estado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reserva (
  id_reserva INT NOT NULL AUTO_INCREMENT,
  id_cliente INT NOT NULL,
  id_depto INT NOT NULL,
  id_sede INT NOT NULL,
  id_estado INT NOT NULL,
  nombre_huesped VARCHAR(150) NOT NULL,
  email_huesped VARCHAR(100) NOT NULL,
  telefono_huesped VARCHAR(30) NULL,
  fecha_ingreso DATE NOT NULL,
  fecha_salida DATE NOT NULL,
  fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_reserva),
  KEY idx_reserva_cliente (id_cliente),
  KEY idx_reserva_depto (id_depto),
  KEY idx_reserva_sede (id_sede),
  KEY idx_reserva_estado (id_estado),
  CONSTRAINT fk_reserva_cliente
    FOREIGN KEY (id_cliente) REFERENCES cliente (id_cliente)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_reserva_departamento
    FOREIGN KEY (id_depto) REFERENCES departamento (id_depto)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_reserva_sede
    FOREIGN KEY (id_sede) REFERENCES sede (id_sede)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_reserva_estado
    FOREIGN KEY (id_estado) REFERENCES estado_reserva (id_estado)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CHECK (fecha_salida >= fecha_ingreso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pre_checkin (
  id_checkin INT NOT NULL AUTO_INCREMENT,
  id_reserva INT NOT NULL,
  tipo_documento VARCHAR(30) NOT NULL,
  num_documento VARCHAR(50) NOT NULL,
  rut_documento VARCHAR(30) NULL,
  hash_contrato VARCHAR(255) NOT NULL,
  acepta_garantia TINYINT(1) NOT NULL DEFAULT 0,
  lp_registro VARCHAR(45) NULL,
  fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_checkin),
  UNIQUE KEY uq_precheckin_reserva (id_reserva),
  CONSTRAINT fk_precheckin_reserva
    FOREIGN KEY (id_reserva) REFERENCES reserva (id_reserva)
    ON UPDATE CASCADE
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE solicitud_servicio (
  id_solicitud INT NOT NULL AUTO_INCREMENT,
  id_reserva INT NOT NULL,
  tipo_servicio VARCHAR(60) NOT NULL,
  cantidad INT NOT NULL DEFAULT 1,
  horario_preferente VARCHAR(60) NULL,
  observaciones TEXT NULL,
  estado_ticket VARCHAR(30) NOT NULL DEFAULT 'Pendiente',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_resolucion DATETIME NULL,
  asignado_a VARCHAR(100) NULL,
  PRIMARY KEY (id_solicitud),
  KEY idx_solicitud_reserva (id_reserva),
  CONSTRAINT fk_solicitud_reserva
    FOREIGN KEY (id_reserva) REFERENCES reserva (id_reserva)
    ON UPDATE CASCADE
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE incidencia (
  id_incidencia INT NOT NULL AUTO_INCREMENT,
  id_reserva INT NOT NULL,
  categoria VARCHAR(80) NOT NULL,
  descripcion TEXT NOT NULL,
  foto_url_1 VARCHAR(255) NULL,
  foto_url_2 VARCHAR(255) NULL,
  foto_url_3 VARCHAR(255) NULL,
  estado VARCHAR(30) NOT NULL DEFAULT 'Abierta',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_cierre DATETIME NULL,
  tecnico_asign VARCHAR(100) NULL,
  PRIMARY KEY (id_incidencia),
  KEY idx_incidencia_reserva (id_reserva),
  CONSTRAINT fk_incidencia_reserva
    FOREIGN KEY (id_reserva) REFERENCES reserva (id_reserva)
    ON UPDATE CASCADE
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE historia_habitacion (
  id_historia INT NOT NULL AUTO_INCREMENT,
  id_depto INT NOT NULL,
  estado_habitacion VARCHAR(30) NOT NULL,
  fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  responsable VARCHAR(150) NULL,
  observacion TEXT NULL,
  PRIMARY KEY (id_historia),
  KEY idx_historia_depto (id_depto),
  CONSTRAINT fk_historia_depto
    FOREIGN KEY (id_depto) REFERENCES departamento (id_depto)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO sede (nombre_sede, direccion, email_contacto) VALUES
  ('Norus Las Condes', 'Av. Las Condes 123', 'lascondes@norus.demo'),
  ('Norus Providencia', 'Av. Providencia 456', 'providencia@norus.demo'),
  ('LUUM', 'Av. Apoquindo 789', 'luum@norus.demo');

INSERT INTO estado_reserva (codigo_estado, descripcion) VALUES
  ('Confirmada', 'Reserva confirmada y activa.'),
  ('Pre check-in', 'El huésped aún debe completar el precheck-in.'),
  ('Activa', 'La reserva está siendo disfrutada actualmente.'),
  ('Check-out', 'La estancia ya finalizó o está en proceso de salida.');

SELECT 'Schema de base de datos Norus creado correctamente.' AS status;
