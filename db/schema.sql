-- ============================================================
-- BOBJ → Datasphere & SAC Converter — HANA Cloud Schema
-- Run once in your HANA Cloud instance (e.g. via DBeaver or BTP HANA Cockpit)
-- Schema: BOBJ_CONVERTER  (create or use an existing schema)
-- ============================================================

-- Optional: create and switch to a dedicated schema
-- CREATE SCHEMA BOBJ_CONVERTER;
-- SET SCHEMA BOBJ_CONVERTER;


-- ─── Projects ─────────────────────────────────────────────────────────────────

CREATE TABLE BOBJ_PROJECTS (
    ID                  NVARCHAR(36)   NOT NULL PRIMARY KEY,   -- UUID
    NAME                NVARCHAR(255)  NOT NULL,
    DESCRIPTION         NCLOB,
    BOBJ_SYSTEM_NAME    NVARCHAR(255),                         -- source BOBJ system label
    DATASPHERE_SPACE_ID NVARCHAR(255),                         -- target space in Datasphere
    SAC_TENANT_URL      NVARCHAR(512),                         -- target SAC tenant
    OWNER_USER_ID       NVARCHAR(255)  NOT NULL,               -- XSUAA sub claim
    CREATED_AT          TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT          TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IDX_PROJECTS_OWNER ON BOBJ_PROJECTS (OWNER_USER_ID);


-- ─── Conversion Jobs ──────────────────────────────────────────────────────────

CREATE TABLE BOBJ_CONVERSION_JOBS (
    ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,    -- UUID
    PROJECT_ID      NVARCHAR(36),                           -- FK → BOBJ_PROJECTS
    ARTIFACT_NAME   NVARCHAR(255)  NOT NULL,
    INPUT_TYPE      NVARCHAR(50)   NOT NULL,                -- universe_xml | report_rpt | manual
    RAW_CONTENT     NCLOB          NOT NULL,                -- uploaded BOBJ artifact text
    STATUS          NVARCHAR(20)   NOT NULL DEFAULT 'pending',  -- pending|running|completed|failed
    RESULT_JSON     NCLOB,                                  -- full AI-generated output (JSON)
    TOTAL_OBJECTS   INTEGER,
    CONVERTED_COUNT INTEGER,
    ERROR_MESSAGE   NVARCHAR(2000),
    OWNER_USER_ID   NVARCHAR(255)  NOT NULL,
    CREATED_AT      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    COMPLETED_AT    TIMESTAMP,

    CONSTRAINT FK_JOB_PROJECT FOREIGN KEY (PROJECT_ID) REFERENCES BOBJ_PROJECTS(ID) ON DELETE SET NULL,
    CONSTRAINT CHK_JOB_STATUS CHECK (STATUS IN ('pending','running','completed','failed')),
    CONSTRAINT CHK_INPUT_TYPE CHECK (INPUT_TYPE IN ('universe_xml','report_rpt','manual'))
);

CREATE INDEX IDX_JOBS_PROJECT   ON BOBJ_CONVERSION_JOBS (PROJECT_ID);
CREATE INDEX IDX_JOBS_OWNER     ON BOBJ_CONVERSION_JOBS (OWNER_USER_ID);
CREATE INDEX IDX_JOBS_STATUS    ON BOBJ_CONVERSION_JOBS (STATUS);
CREATE INDEX IDX_JOBS_CREATED   ON BOBJ_CONVERSION_JOBS (CREATED_AT DESC);


-- ─── Parsed BOBJ Metadata ────────────────────────────────────────────────────
-- Structured store of each parsed BOBJ object (table, class, measure, dimension)

CREATE TABLE BOBJ_METADATA_OBJECTS (
    ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,
    JOB_ID          NVARCHAR(36)   NOT NULL,
    OBJECT_NAME     NVARCHAR(255)  NOT NULL,
    OBJECT_TYPE     NVARCHAR(50)   NOT NULL,    -- Table | Class | Measure | Dimension | Filter | Join
    PARENT_NAME     NVARCHAR(255),              -- parent class or table
    DATA_TYPE       NVARCHAR(100),
    SQL_EXPRESSION  NCLOB,
    IS_KEY          BOOLEAN        DEFAULT FALSE,
    PROPERTIES_JSON NCLOB,                      -- additional properties as JSON

    CONSTRAINT FK_META_JOB FOREIGN KEY (JOB_ID) REFERENCES BOBJ_CONVERSION_JOBS(ID) ON DELETE CASCADE
);

CREATE INDEX IDX_META_JOB  ON BOBJ_METADATA_OBJECTS (JOB_ID);
CREATE INDEX IDX_META_TYPE ON BOBJ_METADATA_OBJECTS (OBJECT_TYPE);


-- ─── Generated Datasphere Entities ───────────────────────────────────────────

CREATE TABLE DATASPHERE_ENTITIES (
    ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,
    JOB_ID          NVARCHAR(36)   NOT NULL,
    ENTITY_NAME     NVARCHAR(255)  NOT NULL,
    ENTITY_TYPE     NVARCHAR(100)  NOT NULL,   -- View | Entity | Dimension | Fact | Analytical Dataset
    DESCRIPTION     NCLOB,
    COLUMNS_JSON    NCLOB          NOT NULL,   -- serialized column definitions
    JOINS_JSON      NCLOB,                     -- serialized join definitions
    SQL_EXPRESSION  NCLOB,
    PUSH_STATUS     NVARCHAR(20)   DEFAULT 'pending',  -- pending | pushed | failed
    PUSH_ERROR      NVARCHAR(1000),
    CREATED_AT      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT FK_DS_JOB FOREIGN KEY (JOB_ID) REFERENCES BOBJ_CONVERSION_JOBS(ID) ON DELETE CASCADE,
    CONSTRAINT CHK_DS_PUSH_STATUS CHECK (PUSH_STATUS IN ('pending','pushed','failed'))
);

CREATE INDEX IDX_DS_JOB  ON DATASPHERE_ENTITIES (JOB_ID);


-- ─── Generated SAC Model Configs ─────────────────────────────────────────────

CREATE TABLE SAC_MODEL_CONFIGS (
    ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,
    JOB_ID          NVARCHAR(36)   NOT NULL,
    MODEL_NAME      NVARCHAR(255)  NOT NULL,
    MODEL_TYPE      NVARCHAR(50)   NOT NULL,    -- Analytical | Planning
    DESCRIPTION     NCLOB,
    CONFIG_JSON     NCLOB          NOT NULL,    -- full model config JSON
    PUSH_STATUS     NVARCHAR(20)   DEFAULT 'pending',
    SAC_MODEL_ID    NVARCHAR(255),              -- ID returned by SAC API after push
    PUSH_ERROR      NVARCHAR(1000),
    CREATED_AT      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT FK_SAC_JOB FOREIGN KEY (JOB_ID) REFERENCES BOBJ_CONVERSION_JOBS(ID) ON DELETE CASCADE,
    CONSTRAINT CHK_SAC_PUSH_STATUS CHECK (PUSH_STATUS IN ('pending','pushed','failed'))
);

CREATE INDEX IDX_SAC_JOB  ON SAC_MODEL_CONFIGS (JOB_ID);


-- ─── Conversion Mapping Audit ─────────────────────────────────────────────────

CREATE TABLE CONVERSION_MAPPING (
    ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,
    JOB_ID          NVARCHAR(36)   NOT NULL,
    SOURCE_OBJECT   NVARCHAR(255)  NOT NULL,
    SOURCE_TYPE     NVARCHAR(100)  NOT NULL,
    TARGET_OBJECT   NVARCHAR(255),
    TARGET_TYPE     NVARCHAR(100),
    STATUS          NVARCHAR(50)   NOT NULL,    -- Converted | Manual Review Required | Not Supported
    NOTES           NVARCHAR(2000),
    FIELD_MAPPINGS  NCLOB,                      -- JSON array of field mapping details

    CONSTRAINT FK_MAP_JOB FOREIGN KEY (JOB_ID) REFERENCES BOBJ_CONVERSION_JOBS(ID) ON DELETE CASCADE,
    CONSTRAINT CHK_MAP_STATUS CHECK (STATUS IN ('Converted','Manual Review Required','Not Supported'))
);

CREATE INDEX IDX_MAP_JOB    ON CONVERSION_MAPPING (JOB_ID);
CREATE INDEX IDX_MAP_STATUS ON CONVERSION_MAPPING (STATUS);


-- ─── User Sessions / Preferences ─────────────────────────────────────────────

CREATE TABLE USER_PREFERENCES (
    USER_ID         NVARCHAR(255)  NOT NULL PRIMARY KEY,    -- XSUAA sub claim
    DEFAULT_PROJECT NVARCHAR(36),
    DS_SPACE_ID     NVARCHAR(255),
    SAC_TENANT_URL  NVARCHAR(512),
    PREFERENCES_JSON NCLOB,                                 -- UI preferences JSON
    LAST_SEEN_AT    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CREATED_AT      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ─── Audit Log ────────────────────────────────────────────────────────────────

CREATE TABLE AUDIT_LOG (
    ID          NVARCHAR(36)   NOT NULL PRIMARY KEY DEFAULT SYSUUID,
    USER_ID     NVARCHAR(255)  NOT NULL,
    ACTION      NVARCHAR(100)  NOT NULL,   -- e.g. JOB_CREATED, ENTITY_PUSHED, PROJECT_DELETED
    RESOURCE_ID NVARCHAR(36),
    DETAILS     NCLOB,                     -- JSON blob with action details
    IP_ADDRESS  NVARCHAR(45),
    CREATED_AT  TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IDX_AUDIT_USER    ON AUDIT_LOG (USER_ID);
CREATE INDEX IDX_AUDIT_ACTION  ON AUDIT_LOG (ACTION);
CREATE INDEX IDX_AUDIT_CREATED ON AUDIT_LOG (CREATED_AT DESC);
