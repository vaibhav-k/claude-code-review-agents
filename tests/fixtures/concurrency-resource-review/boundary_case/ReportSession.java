class ReportSession implements AutoCloseable {
    private final Connection conn = pool.getConnection();
    ResultSet run(String sql) throws SQLException { return conn.createStatement().executeQuery(sql); }
    public void close() throws SQLException { conn.close(); }
}
