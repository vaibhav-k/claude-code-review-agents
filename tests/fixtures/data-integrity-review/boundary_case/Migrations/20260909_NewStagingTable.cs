migrationBuilder.CreateTable(
    name: "StagingImports",
    columns: table => new { BatchLabel = table.Column<string>(type: "varchar(20)") });
